# -*- coding: utf-8 -*-
"""
Python 2.7
Created on Mon Aug 21 06:40:22 2017
@author: nlace
"""

import cherrypy
from cherrypy.process.plugins import BackgroundTask
import time
import StringIO


import os, shutil
import PIL
from PIL import Image
import subprocess
from mako.template import Template
from time import gmtime, strftime

import sqlite3
from sqlite3worker import Sqlite3Worker

import pyotp
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import urllib
import random, time, socket
from mako.lookup import TemplateLookup
from mako import exceptions
import qrcode

from PyPDF2 import PdfFileMerger, PdfFileReader
#get servername for diag array
servername = socket.gethostname()

root="https://www.nick-lace.com"
base='/legiondaycare'

root=""
base=''

dpath = root + base

infrate = 2.95 # 0-18 month rate
chrate = 2.85 #default child rate 18 mo and above
r4cs = 4.24  #4cs hourly rate

cyclestart = 1 #billing cycle starts on the 5th of the month



password = 'daycare'  #opens ui

from Queue import Queue
from threading import Thread



#setup tables with single connection
conn = sqlite3.connect('stuff.db')
conn.isolation_level = None
c = conn.cursor()  
c.execute('create table if not exists providers ( ID INTEGER PRIMARY KEY AUTOINCREMENT, name, phone, rate,photo, ft , status NUMERIC)')
c.execute('create table if not exists adults ( ID INTEGER PRIMARY KEY AUTOINCREMENT, name, address, phone, email, pin, password, workplace, workphone, econtact)')
c.execute('CREATE TABLE if not exists "children" ( `ID` INTEGER PRIMARY KEY AUTOINCREMENT, `name` TEXT, `parentid` TEXT, `parentidb` TEXT, `age` TEXT, `restrictions` TEXT, allergies TEXT, `rate` TEXT, `photo` TEXT, `status` NUMERIC, dob TEXT, is4cs NUMERIC, immunizations )')
c.execute('create table if not exists activity ( ID INTEGER PRIMARY KEY AUTOINCREMENT, kind, time, ref)')
c.execute('create table if not exists schedule ( ID INTEGER PRIMARY KEY AUTOINCREMENT, day, providers, children)')
c.execute('create table if not exists ctimeclock ( ID INTEGER PRIMARY KEY AUTOINCREMENT , dt, plaindate, punchtype, punchtime, childid,  ref)')
c.execute('create table if not exists pperm ( ID INTEGER PRIMARY KEY AUTOINCREMENT, parent INTEGER, childid INTEGER, kind TEXT, approval TEXT)')
c.execute('create table if not exists ctempclock ( ID INTEGER PRIMARY KEY AUTOINCREMENT , dt, plaindate, punchtype, punchtime, childid,  ref)')
c.execute('create table if not exists invoices ( ID INTEGER PRIMARY KEY AUTOINCREMENT , dt, childid, fname, amt NUMERIC)')
c.execute('create table if not exists ledger ( ID INTEGER PRIMARY KEY AUTOINCREMENT , dt, childid, desc, amt NUMERIC)')
c.execute('create table if not exists users ( ID INTEGER PRIMARY KEY AUTOINCREMENT, name, pw, key)')

c.execute('create table if not exists settings ( ID INTEGER PRIMARY KEY AUTOINCREMENT, name, value)')

c.execute('create table if not exists ptimeclock ( ID INTEGER PRIMARY KEY AUTOINCREMENT , dt, plaindate, punchtype, punchtime, provid,  ref)')


conn.commit()
conn.close()

#open threadpool connection for database to handle multiple simultaneous requests
ct = Sqlite3Worker('stuff.db')




#tell cherrypy to listen on port 80 and host 0.0.0.0 
#these settings can be overridden in the config file at the bottom of the program
cherrypy.config.update({'server.socket_host': '0.0.0.0',
                        'server.socket_port': 80,
                    })

#declare the mako template lookup should be in the templates folder
mylookup = TemplateLookup(directories=['templates'])


class StringGenerator(object):
    @cherrypy.expose
    def index(self):
        checksession()
        children = ct.execute('select ID, name, photo, restrictions from children where status=1')
        providers = ct.execute('select ID, name from providers where status=1')
        
        activity = ct.execute('select kind, time from activity order by ID desc limit 10')
        mytemplate = Template(filename='templates/index.html', lookup=mylookup)
        lerender = mytemplate.render(children=children, pv=providers, activity=activity)
        return lerender
    index.exposed = True

    @cherrypy.expose
    def parents(self):
        checksession()
        parents = ct.execute('select ID, name, phone, email, pin, address, workplace, workphone from adults')
        mytemplate = Template(filename='templates/parents.html', lookup=mylookup)
        lerender = mytemplate.render(parents=parents)
        return lerender

    @cherrypy.expose
    def invoice(self, rid, adj=0, concat=0, pdf=0):
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        tstart = time.time()        
        checksession()
        pdf = int(pdf)
        concat = int(concat)
        adj = int(adj)
        today = datetime.today()
        monthbegin = datetime(today.year, today.month, 1) + relativedelta(months=adj)

        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
        print( 'xxxx', lastmonth, monthbegin, nextmonth      )
        
        cinfo = ct.execute('SELECT children.name, children.status, children.ID, children.dob, children.restrictions, children.is4cs, children.rate from  children where children.ID="%s"' % rid)
        c = cinfo[0]
        pinfo = ct.execute('SELECT adults.name, adults.address, adults.phone, pperm.kind from adults, children,  pperm WHERE adults.ID = pperm.parent AND pperm.childid=children.ID  AND pperm.kind="BParent" AND children.ID=%s' % rid)
        invoices = ct.execute('SELECT ID, fname, amt from invoices where childid="%s" order by dt desc' % rid)

        cage =   c[3]          
        is4cs = c[5]

        rover = float(c[6])

        timeinfo, ctotal = docalcuations(c[2], cage, is4cs, lastmonth,monthbegin,concat, rover)
        ctotal = "%.2f" %  ctotal
        child= [c[0],c[1],c[2],c[3],c[4], timeinfo, ctotal]
        month = lastmonth.strftime('%B %Y')
        mytemplate = Template(filename='templates/invoice.html', lookup=mylookup)
        
        diag = [time.time() - tstart, servername, datetime.now()]

        
        #prerender page
        try:
            lerender = mytemplate.render(c=child, lm=lastmonth.date(), mb=monthbegin.date(), p1=pinfo, adj=adj, month=month, rid=rid, invoices=invoices, diag=diag)
            
        except:
            return exceptions.html_error_template().render()
                
        #if we want a pdf...             
        if pdf==1:

            #take invoice amounts and enter into ledger
            ledgeraddinvoice(rid, month, ctotal, monthbegin)
            #build pdf using latex and return to the session
            return buildpdf(rid, month, ctotal, monthbegin, lastmonth, child, pinfo, adj, c)

        else:
            return lerender

    @cherrypy.expose
    def endofyear(self, rid):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")
        today = datetime.today()
        monthbegin = datetime(today.year, 1, 1) 

        lastmonth = monthbegin -  relativedelta(years=1) - relativedelta(days=1)
        
        #adjust endpoint for ledger function
        monthbegin = monthbegin -  relativedelta(months=1) -  relativedelta(days=1)
        
        nextmonth = monthbegin +  relativedelta(months=1)
        print 'xxxx', lastmonth, monthbegin, nextmonth      
        
        cinfo = ct.execute('SELECT children.name, children.status, children.ID, children.dob, children.restrictions, children.is4cs from  children where children.ID="%s"' % rid)
        c = cinfo[0]
        pinfo = ct.execute('SELECT adults.name, adults.address, adults.phone, pperm.kind from adults, children,  pperm WHERE adults.ID = pperm.parent AND pperm.childid=children.ID  AND pperm.kind="BParent" AND children.ID=%s' % rid)



        child= [c[0],c[1],c[2],c[3],c[4], 0, 0]
        month = monthbegin.strftime('%Y')
        
       
        adj=0
 
    
        texname = "%s EOY %s.tex" % (month, c[0])
        texpath = 'invoice/%s' % texname
        pdfname = "%s EOY %s.pdf" % (month, c[0])
        pdfpath = 'invoice/%s' % pdfname
        
        
        #invid = invoiceaddupdate(pdfname, rid, ctotal)
        
        ledger, linfo = renderledger(rid, lastmonth ,monthbegin)
        
  
        mytemplate = Template(filename='templates/endofyear.html', lookup=mylookup)
        lerender = mytemplate.render(c=child, lm=lastmonth.date(), mb=monthbegin.date(), p1=pinfo, adj=adj, month=month, rid=rid,  ledger=ledger, linfo=linfo)
        
        #take the rendered template and save it
        with open(texpath, 'w') as f:
            f.write(lerender)
        
        proc = subprocess.Popen('pdflatex "%s"' % (texname) ,shell=True, cwd="invoice")
        tick=0
        while proc.poll() == None:
            time.sleep(1)
            tick=tick+1
            if tick > 10:
                proc.terminate()
                print('Terminating Process')
                return rendererror("PDf generation process ran too long, the system may be overloaded or there may be some illegal characters (#,$,\,etc) in the render see below <br><pre>%s</pre>" % lerender)
        
        
        buffer = StringIO.StringIO()        
        cherrypy.response.headers['Content-Type'] = "application/pdf"
        buffer.write(open(pdfpath, "rb").read())
            
    
        return buffer.getvalue()      
        




    @cherrypy.expose
    def parent(self, pid, pname=None, **kwargs):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")
        
        today = datetime.today()
        monthbegin = datetime(today.year, today.month, 1)

        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
        print 'xxxx', lastmonth, monthbegin, nextmonth      
        if pname <> None:
            print kwargs
            sql = 'update adults set name="%s", phone="%s", email="%s", address="%s", workplace="%s", workphone="%s", econtact="%s" where ID=%s' % (pname, kwargs['phone'],kwargs['email'],kwargs['address'],kwargs['workplace'],kwargs['workphone'],kwargs['econtact'],pid)
            print sql
            ct.execute(sql)
        
        
        
        parents = ct.execute('select name, phone, email, pin, address, workplace, workphone, ID, econtact from adults WHERE ID=%s' % pid)
        parents = parents[0]
        
        sql  = 'SELECT children.name, children.status, children.ID, children.dob, children.restrictions, children.is4cs, children.rate from  children, pperm, adults where pperm.parent=adults.ID AND pperm.childid=children.ID AND pperm.kind="BParent" AND pperm.parent="%s"' % pid
        print sql
        cinfo = ct.execute(sql)
        children = []
        for c in cinfo:
            
            cage =   c[3]          
            is4cs = c[5]
            rover = float(c[6])
            timeinfo, ctotal = docalcuations(c[2], cage, is4cs, monthbegin,nextmonth,0, rover)
            ctotal = "%.2f" %  ctotal
            child= [c[0],c[1],c[2],c[3],c[4], timeinfo, ctotal]
            children.append(child)

        print children
        
        mytemplate = Template(filename='templates/parent.html', lookup=mylookup)
        lerender = mytemplate.render(p=parents, children=cinfo, x=children)
        return lerender
    @cherrypy.expose
    def parentlm(self, pid):
        checksession()
        
        
        today = datetime.today()
        monthbegin = datetime(today.year, today.month, cyclestart)

        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
        print 'xxxx', lastmonth, monthbegin, nextmonth      
        
        
        
        parents = ct.execute('select name, phone, email, pin from parents WHERE ID=%s' % pid)
        parents = parents[0]
        
        
        cinfo = ct.execute('SELECT children.name, children.status, children.ID, children.dob, children.restrictions, children.is4cs, children.rate from  children where children.parentid="%s" and children.active=1' % pid)
        children = []
        for c in cinfo:
            
            cage =   c[3]          
            is4cs = c[5]
            rover = float(c[6])
            
            timeinfo, ctotal = docalcuations(c[2], cage, is4cs, lastmonth, monthbegin,0,rover)
            ctotal = "%.2f" %  ctotal
            child= [c[0],c[1],c[2],c[3],c[4], timeinfo, ctotal]
            children.append(child)

        print children
        
        mytemplate = Template(filename='templates/parent.html', lookup=mylookup)
        lerender = mytemplate.render(p=parents, children=cinfo, x=children)
        return lerender
    @cherrypy.expose
    def login(self, pw=None, user=None):
        
        leip =   ""
        users = ct.execute("select ID, name from users order by name asc")


                

        
        
        dmerp = "%s:%s" % (str(datetime.now()), "x")
        
        diag = [leip, servername, dmerp]                
        mytemplate = Template(filename='templates/login.html', lookup=mylookup)
        loginpage = mytemplate.render(users=users, ip=diag)
        if pw == None:        
            print 'nopw'
            return loginpage
        else:

            
            leuser = ct.execute('select pw, key, name from users where ID=%s' % user)
            leuser = leuser[0]
            password = leuser[0]
            key = leuser[1]
            name = leuser[2]

            

            if pw == password:

                cherrypy.session['active'] = True
                cherrypy.session['user'] = name
                cherrypy.session['userid'] = user
                cherrypy.session['level'] = "Admin"
                action("User %s has logged in" % name,"System")
                raise cherrypy.HTTPRedirect(dpath + "/index")
   
            else:
                return loginpage
    @cherrypy.expose
    def logout(self):
        cherrypy.session['active'] = False
        raise cherrypy.HTTPRedirect(dpath + "/login")             
        
    @cherrypy.expose
    def newprovider(self, name=None, photo=None, phone=None, rate=None):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")   
        if name != None:
            #add the provider.

            
            
            
            ct.execute("Insert into providers (name, phone, status, rate) values ('%s', '%s', 0, '%s')" % (name, phone, rate))
            rid = ct.execute("SELECT ID from providers where name='%s' AND phone='%s'" % (name, phone))
            rid = rid[0][0]
            
            target = os.path.join(os.path.dirname(os.path.realpath(__file__)), "res")
            target = os.path.join(target,"provider")
            target = os.path.join(target,"p%s.jpg" % rid)
            try:

                assert isinstance(photo, cherrypy._cpreqbody.Part)
                photoupload(photo,target)
                sql = 'update providers set photo="%s" where ID=%s' % ("p%s.jpg" % rid, rid)
                ct.execute(sql)
            except:
                pass
            
            
            raise cherrypy.HTTPRedirect(dpath + "/index")
        
        else:
            
            mytemplate = Template(filename='templates/newprovider.html', lookup=mylookup)
            lerender = mytemplate.render()
            return lerender  
    @cherrypy.expose
    def provideredit(self,rid, phone=None, name=None, photo=None, rate=None):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")           
        if phone != None:
            
            target = os.path.join(os.path.dirname(os.path.realpath(__file__)), "res")
            target = os.path.join(target,"provider")
            target = os.path.join(target,"p%s.jpg" % rid)
            try:
                assert isinstance(photo, cherrypy._cpreqbody.Part)
                photoupload(photo,target)
                
                sql = 'update providers set photo="%s" where ID=%s' % ("p%s.jpg" % rid, rid)
                ct.execute(sql)
            except:
                pass
            ct.execute('UPDATE providers set phone="%s" where ID=%s'% (phone,rid))
            ct.execute('UPDATE providers set name="%s"  where ID=%s'% (name,rid))
            ct.execute('UPDATE providers set rate="%s" where ID=%s'% (rate,rid))

        #start regular stuffs
            
            
            
        provider = ct.execute('select name, phone, rate from providers where ID=%s' % rid)
        provider = provider[0]


        today = datetime.today()
        adj = 0
        monthbegin = datetime(today.year, today.month, 1) + relativedelta(months=adj)

        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
  


        timeinfo, ptotal, tlh = dotimeclockcalcsprovider(rid, provider[2], lastmonth, nextmonth,concat=0)

        ptotal = "%0.3f" % ptotal
        mytemplate = Template(filename='templates/provideredit.html', lookup=mylookup)
        lerender = mytemplate.render(rid=rid, provider=provider,ptotal=ptotal, timeinfo=timeinfo)
        return lerender  
    
    @cherrypy.expose
    def ppromote(self,cid, pid):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")     
        
        action("Promoting parent id %s to bparent" % pid,cid)

        #nuke previous billing parent
        sql = "UPDATE pperm set kind='parent' where childid=%s and kind='BParent'"% (cid)
        print sql
        ct.execute(sql)

        #promote to billing parent
        ct.execute('UPDATE pperm set kind="BParent" where ID=%s'% (pid))

        
        raise cherrypy.HTTPRedirect(dpath + "/childedit?rid=%s"%cid)

    
    @cherrypy.expose
    def childedit(self,rid, phone=None, name=None, photo=None, parent=None, age=None, restrictions=None, rate=None, is4cs=None, active=None):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")           
        if name != None:
            target = os.path.join(os.path.dirname(os.path.realpath(__file__)), "res")
            target = os.path.join(target,"child")
            target = os.path.join(target,"p%s.jpg" % rid)
            
            try:
                assert isinstance(photo, cherrypy._cpreqbody.Part)
                photoupload(photo,target)
                sql = 'update children set photo="%s" where ID=%s' % ("p%s.jpg" % rid, rid)
                print sql
                ct.execute(sql)
            except:
                pass
  
            if is4cs == "on":
                ct.execute('UPDATE children set is4cs=1 where ID=%s'% (rid))

            else:
                ct.execute('UPDATE children set is4cs=0 where ID=%s'% (rid))

            if active == "on":
                ct.execute('UPDATE children set active=1 where ID=%s'% (rid))

            else:
                ct.execute('UPDATE children set active=0 where ID=%s'% (rid))

      
      
            ct.execute('UPDATE children set dob="%s" where ID=%s'% (age,rid))
            ct.execute('UPDATE children set name="%s"  where ID=%s'% (name,rid))
            
            
            if rate =="":
                rate = 0
            
            ct.execute('UPDATE children set rate="%s"  where ID=%s'% (rate,rid))
            
            ct.execute('UPDATE children set restrictions="%s"  where ID=%s'% (restrictions,rid))
            raise cherrypy.HTTPRedirect(dpath + "/child?rid=%s"% rid)
 
        child = ct.execute('select name, dob, restrictions, rate, status, is4cs, active from children where ID=%s' % rid)
        print child
        
        child = child[0]
        is4cs=child[5]
        active = child[6]
        #to allow checkbox to appear checked        
        checkboxes = []
        if is4cs == 1:
            is4cs='checked'
            checkboxes.append('checked')
        else:
            is4cs=''
            checkboxes.append('')

        if active == 1:
            checkboxes.append('checked')
        else:
            checkboxes.append('')

        sql = "select adults.name, adults.address, adults.phone, pperm.ID, pperm.kind from adults,  pperm WHERE adults.ID = pperm.parent AND pperm.childid=%s" % rid
        print sql        
        p = ct.execute(sql)        
        
        adults = ct.execute('select ID, name from adults order by ID Desc')        

        today = datetime.today()
        lastmonth = today -  relativedelta(months=1)


        lm = lastmonth.strftime('%B')
        lmb = lastmonth.strftime('%m')
        tm = today.strftime('%B')
        tmb = today.strftime('%m')
        
        tinfo= [lm, lmb, tm, tmb]

        #timeclock info for Student..
        
        sql = 'SELECT ctimeclock.punchtime, ctimeclock.ref, children.name, ctimeclock.ID, children.ID from ctimeclock, children where ctimeclock.childid=children.ID and ctimeclock.punchtype="duration" and children.ID=%s order by ctimeclock.plaindate desc, ctimeclock.ref desc limit 500' % rid
        tcinfo = ct.execute(sql)
        
        mytemplate = Template(filename='templates/childedit.html', lookup=mylookup)
        lerender = mytemplate.render(rid=rid, child=child,is4cs=is4cs,p=p, adults=adults, tinfo=tinfo,tcinfo=tcinfo, checkboxes=checkboxes)
        return lerender   
    @cherrypy.expose
    def child(self,rid):
        tstart = time.time()
        checksession()
        child = ct.execute('select name, dob, restrictions, rate, status,  allergies from children  where ID=%s' % rid)
        child = child[0]

        dob = child[1]
        getchildmonths(dob)
        
        sql = "select adults.name, adults.address, adults.phone, pperm.ID, pperm.kind, adults.pin from adults,  pperm WHERE adults.ID = pperm.parent AND pperm.childid=%s" % rid
        print sql        
        p = ct.execute(sql)          
        
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        sql = 'select kind, time from activity where ref="%s" and ' % rid
        sql = sql + ' time LIKE "%' + today + '%" order by ID DESC'
        print sql
        act = ct.execute(sql)        
        
        
        
        mytemplate = Template(filename='templates/child.html', lookup=mylookup)
        diag = [time.time() - tstart, servername, datetime.now()]

        lerender = mytemplate.render(rid=rid, child=child, act=act, p=p, diag=diag)
        return lerender   
    @cherrypy.expose
    def addperm(self, pid=None, rid=None, type=None, approval=None):
        checksession()
        
        sql = 'INSERT INTO pperm (parent, childid, kind, approval) values (%s, %s, "%s", "%s")' % (pid, rid, type, approval)
        print sql
        ct.execute(sql)
        
        raise cherrypy.HTTPRedirect(dpath + "/childedit?rid=%s"%rid)

    @cherrypy.expose
    def newparent(self, name=None, photo=None, phone=None, email=None, **kwargs):
        checksession()
                
        if name != None:
            #add the parent.



            #make a unique pin
            cnt = 1
            while cnt > 0:
                pin = random.randrange(10000, 99999, 1)
                cnt=ct.execute('select count(*) parents where pin="%s"' % pin)
                cnt=[0][0]
                
                
            address = degunk(kwargs,'address',None)
                
            econtact = degunk(kwargs,'emergencycontact',None)
            workphone = degunk(kwargs,'workphone',None)
            workplace = degunk(kwargs,'workplace',None)
#            is4cs = degunk(kwargs,'is4cs',0)
#
#            if is4cs == "on":
#                is4cs = 1

            ct.execute("Insert into adults (name, phone, email, pin,  workplace, workphone, econtact, address) values ('%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s')" % (name, phone, email, pin,  workplace, workphone, econtact, address))
            
            #dumb, but the sqlite serializer doesn't expose lastrowid...
            rid = ct.execute("SELECT ID from adults where name='%s' AND phone='%s'" % (name, phone))
            rid = rid[0][0]
            try:
                assert isinstance(photo, cherrypy._cpreqbody.Part)
                cherrypy.response.timeout = 3600
                target = "res\\parent\\p%s.jpg" % rid
                photoupload(photo,target)
            except:
                pass
            
            
            raise cherrypy.HTTPRedirect(dpath + "/index")
        
        else:
            
            mytemplate = Template(filename='templates/newparent.html', lookup=mylookup)
            lerender = mytemplate.render()
            return lerender  
    @cherrypy.expose
    def newchild(self, name=None, photo=None, parent=None, dob=None, restrictions=None, rate=None, **kwargs):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")           
        if name != None:
  
            try:
                
                getchildmonths(dob)
            except:
                return rendererror("Incorrectly formatted DOB, MUST be YYYY-MM-DD")
                
            
            
            allergies=degunk(kwargs,'allergies',None)
            immunization=degunk(kwargs,'immunization',0)
            parentb=degunk(kwargs,'parentb',None)

            if immunization == 'on':
                immunization = 1

            sql = 'insert into children (rate, name, dob, parentid, restrictions, status, parentidb, allergies, immunizations, active) values ("0", "%s","%s","%s","%s",0,"%s","%s","%s",1)' % (name, dob, parent, restrictions, parentb, allergies, immunization)
            print sql
            ct.execute(sql)
            
            sql = 'SELECT ID from children where name="%s" AND parentid="%s"' % (name, parent)
            print sql
            rid=ct.execute(sql)
            rid=rid[0][0]
            
            sql = 'INSERT INTO pperm (parent, childid, kind, approval) values (%s, %s, "BParent", "Contract")' % (parent, rid)
            print sql
            ct.execute(sql)
            
            target = os.path.join(os.path.dirname(os.path.realpath(__file__)), "res")
            target = os.path.join(target,"child")
            target = os.path.join(target,"p%s.jpg" % rid)
            
            try:
                assert isinstance(photo, cherrypy._cpreqbody.Part)
                photoupload(photo,target)
                sql = 'update children set photo="%S" where ID=%s' % ("p%s.jpg" % rid, rid)
                print sql
                rid=ct.execute(sql)
            except:
                pass
            
            raise cherrypy.HTTPRedirect(dpath + "/childedit?rid=%s"%rid)
      
      
      
        parents = ct.execute('select ID, name from adults order by ID Desc')
        mytemplate = Template(filename='templates/newchild.html', lookup=mylookup)
        lerender = mytemplate.render(parents=parents)
        return lerender  
    @cherrypy.expose
    def action(self,rid, action):
        checksession()
        now = datetime.now()
        now = now.strftime('%Y-%m-%d %I:%M:%S %p')
        ct.execute('insert into activity (kind, time, ref) values ("%s","%s","%s")' % (action,now,rid))
        raise cherrypy.HTTPRedirect(dpath + "/child?rid=%s" % rid)
    @cherrypy.expose
    def pcheckin(self, provid=None):
        checksession()
            

        if provid != None:
            #ploginsession()
            sql ='SELECT status, name from providers where ID=%s' % provid
            print sql 
            cnt = ct.execute(sql)
            rec = cnt[0]
            cnt = rec[0]
            print 'update child ',cnt
            if cnt ==1:
                cnt = 0
                pclockout(provid)
            else:
                cnt = 1
                
                pclockin(provid)
            
            ct.execute('update providers set status="%s" where ID=%s' % (cnt,provid))
        raise cherrypy.HTTPRedirect(dpath + "/checkin")

    @cherrypy.expose
    def checkin(self,pin=None, childid=None, parent=None):
        checksession()
        provinfo = ct.execute('SELECT providers.name, providers.status, providers.ID, providers.photo from  providers ')
        cinfo = ct.execute('SELECT children.name, children.status, children.ID, children.photo from  children where children.active=1 ')
        
        #check if somebody has been in too long!
        errors = checkpuncherrors()
        
        e2 = check_employee_timeerrors()
        for err in e2:
            errors.append(err)
        e2 = check_child_timeerrors()
        for err in e2:
            errors.append(err)


      
        
        
        mytemplate = Template(filename='templates/checkinc.html', lookup=mylookup)
        diag = [servername, datetime.now(), cherrypy.session['level']]

        lerender = mytemplate.render(info=cinfo,provinfo=provinfo, diag=diag, errors=errors)
        

        if childid != None:
            #ploginsession()
            sql ='SELECT status, name from children where ID=%s' % childid
            print sql 
            cnt = ct.execute(sql)
            rec = cnt[0]
            cnt = rec[0]
            print 'update child ',cnt
            if cnt ==1:
                cnt = 0
                action("%s checked out by %s" % (rec[1], parent),childid)
                clockout(childid)
            else:
                cnt = 1
                action("%s checked in by %s" % (rec[1], parent),childid)
                clockin(childid)
            
            ct.execute('update children set status="%s" where ID=%s' % (cnt,childid))
            raise cherrypy.HTTPRedirect(dpath + "/checkin")
        return lerender
        
        
        #NOT SURE WHY THIS IS HERE,  THIS WILL ALWAYS RETURN LERENDER ABOVE...
        mytemplate = Template(filename='templates/checkin.html', lookup=mylookup)

        if pin == None:
            #render the checkin page
            lerender = mytemplate.render()
            cherrypy.session['pactive'] = False
        else:
            cnt = ct.execute('SELECT count(*) from parents where pin="%s"' % pin)
            cnt = cnt[0]
            cnt = cnt[0]
            if cnt > 0:
                cherrypy.session['pactive'] = True
                p = ct.execute('SELECT ID, name, phone, email from parents where pin="%s"' % pin)
                p = p[0]

                cinfo = ct.execute('SELECT children.name, children.status, children.ID from  children where children.parentid="%s"' % p[0])
                mytemplate = Template(filename='templates/checkinb.html', lookup=mylookup)
                lerender = mytemplate.render(info=cinfo, p=p, pin=pin)
            else:
                lerender = mytemplate.render() 
        return lerender   


    @cherrypy.expose
    def parentcheckin(self, pin=None, childid=None, parent=None):
        
        print pin
        if childid != None:
            ploginsession()
            sql ='SELECT status, name from children where ID=%s' % childid
            print sql 
            cnt = ct.execute(sql)
            rec = cnt[0]
            cnt = rec[0]
            print 'update child ',cnt
            if cnt ==1:
                cnt = 0
                action("%s checked out by %s" % (rec[1], parent),childid)
                clockout(childid)
            else:
                cnt = 1
                action("%s checked in by %s" % (rec[1], parent),childid)
                clockin(childid)
            
            ct.execute('update children set status="%s" where ID=%s' % (cnt,childid))
            

        
        mytemplate = Template(filename='templates/checkin.html', lookup=mylookup)

        if pin == None:
            #render the checkin page
            lerender = mytemplate.render()
            cherrypy.session['pactive'] = False
        else:
            cnt = ct.execute('SELECT count(*) from adults where pin="%s"' % pin)
            cnt = cnt[0]
            cnt = cnt[0]
            if cnt > 0:
                print 'setting parent session as active'
                cherrypy.session['pactive'] = True
                p = ct.execute('SELECT ID, name, phone, email from adults where pin="%s"' % pin)
                p = p[0]

                cinfo = ct.execute('SELECT children.name, children.status, children.ID, pperm.kind, children.photo from adults, children,  pperm WHERE adults.ID = pperm.parent AND pperm.childid=children.ID AND adults.pin=="%s" ORDER BY pperm.kind ASC' % pin)
                mytemplate = Template(filename='templates/checkinb.html', lookup=mylookup)
                lerender = mytemplate.render(info=cinfo, p=p, pin=pin)
            else:
                lerender = mytemplate.render() 
        return lerender   


    @cherrypy.expose
    def children(self, allch=0):
        checksession()
        if allch==0:
            cinfo = ct.execute('SELECT name, status, ID, photo from  children where children.active=1')
        else:
            cinfo = ct.execute('SELECT name, status, ID, photo from  children ')
        mytemplate = Template(filename='templates/allchildren.html', lookup=mylookup)
        lerender = mytemplate.render(info=cinfo)
       
        return lerender

    @cherrypy.expose
    def cpunchmanual(self,rid, **kwargs):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        try:
            month= kwargs['month']
            day = kwargs['day']
            
            inampm = int(kwargs['inampm'])
            outampm = int(kwargs['outampm'])

            inhour = int(kwargs['inhour'])
            inminute =int( kwargs['inminute'])
            
            outhour = int(kwargs['outhour'])
            outminute =int( kwargs['outminute'])
            
            
            if inampm ==1:
                if inhour < 12:
                    inhour = inhour + 12
               
                
            if outampm ==1:
                if outhour < 12:
                    outhour = outhour + 12
                
            
            
            now = datetime.now()
            year = int(kwargs['year'])

        except:
            
            return rendererror('Formatting Error - try again')
            pass

        instr = "%s-%s-%s %s:%s" % (year,month, day, inhour, inminute)
        outstr = "%s-%s-%s %s:%s" % (year,month, day, outhour, outminute)

        
        try:
            intime = datetime.strptime(instr, '%Y-%m-%d %H:%M')
            outtime = datetime.strptime(outstr, '%Y-%m-%d %H:%M')
        except:
            return rendererror('Somehow got a date that makes no sense %s %s' % (instr, outstr))
            pass
        
        
        diff = outtime - intime
        diff = diff.total_seconds()
        if diff < 0:
            return rendererror("For some reason the duration is negative... Finish must be after start")
            #need to add some time
            outstr = "%s-%s-%s %s:%s" % (year,month, day, outhour+12, outminute)
            outtime = datetime.strptime(outstr, '%Y-%m-%d %H:%M')
            diff = outtime - intime
            diff = diff.total_seconds()
        
        durhours = diff/3600
        

        ref = "%s to %s" % (intime, outtime)
        ct.execute('insert into ctempclock (punchtype, punchtime, childid, dt, ref, plaindate) values ("duration","%s","%s","%s","%s","%s")' % (durhours,rid,outtime,ref,outtime.date()))
    
        x = ct.execute('select ctempclock.punchtime, ctempclock.ref, ctempclock.ID, children.name from ctempclock, children  where ctempclock.childid="%s" AND ctempclock.childid=children.ID order by ctempclock.ID DESC LIMIT 1' % rid)
        
        
        intime=intime.strftime('%Y-%m-%d %I:%M:%S %p')
        outtime = outtime.strftime('%Y-%m-%d %I:%M:%S %p')        
        
        
        
        cinfo = ct.execute('SELECT name, status, ID, photo from  children where ID=%s' % rid)
        cinfo = cinfo[0]

        mytemplate = Template(filename='templates/tcconfirm.html', lookup=mylookup)
        lerender = mytemplate.render(durhours=durhours, intime=intime, outtime=outtime, cinfo=cinfo, rid=rid, x=x)
               
        return lerender
        
        
    @cherrypy.expose
    def ppunchmanual(self,rid, **kwargs):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        try:
            month= kwargs['month']
            day = kwargs['day']
            
            inampm = int(kwargs['inampm'])
            outampm = int(kwargs['outampm'])

            inhour = int(kwargs['inhour'])
            inminute =int( kwargs['inminute'])
            
            outhour = int(kwargs['outhour'])
            outminute =int( kwargs['outminute'])
            
            
            if inampm ==1:
                if inhour < 12:
                    inhour = inhour + 12
               
                
            if outampm ==1:
                if outhour < 12:
                    outhour = outhour + 12
                
            
            
            now = datetime.now()
            year = now.year
            
            
        except:
            
            return rendererror('Formatting Error - try again')
            pass

        instr = "%s-%s-%s %s:%s" % (year,month, day, inhour, inminute)
        outstr = "%s-%s-%s %s:%s" % (year,month, day, outhour, outminute)

        
        try:
            intime = datetime.strptime(instr, '%Y-%m-%d %H:%M')
            outtime = datetime.strptime(outstr, '%Y-%m-%d %H:%M')
        except:
            return rendererror('Somehow got a date that makes no sense %s %s' % (instr, outstr))
            pass
        
        
        diff = outtime - intime
        diff = diff.total_seconds()
        if diff < 0:
            return rendererror("For some reason the duration is negative... Finish must be after start")
            #need to add some time
            outstr = "%s-%s-%s %s:%s" % (year,month, day, outhour+12, outminute)
            outtime = datetime.strptime(outstr, '%Y-%m-%d %H:%M')
            diff = outtime - intime
            diff = diff.total_seconds()
        
        durhours = diff/3600
        

        ref = "%s to %s" % (intime, outtime)
        
        sql = 'insert into ptimeclock (punchtype, punchtime, provid, dt, ref, plaindate) values ("duration","%s","%s","%s","%s","%s")' % (durhours,rid,outtime,ref,outtime.date())
        out  = ct.execute(sql)
        raise cherrypy.HTTPRedirect(dpath + "/pmanualpunch")
        

        
    @cherrypy.expose
    def cpunchmanualsubmit(self,rid, tpunch):
        checksession()
        rid = int(rid)
        tpunch = int(tpunch)
        
        
        sql ="select dt, plaindate, punchtime, childid, `ref`, punchtype from ctempclock where ID=%s" % tpunch
        x = ct.execute(sql)
        for xx in x:
            dt = xx[0]
            plaindate = xx[1]
            punchtime = xx[2]
            childid= xx[3]
            ref = xx[4]
            punchtype = xx[5]

            sql = "insert into ctimeclock (dt, plaindate, punchtime, childid, `ref`, punchtype) values ('%s','%s','%s','%s','%s','%s')" % (dt, plaindate, punchtime, childid, ref, punchtype)
            print sql
            ct.execute(sql)
            sql = "delete from ctempclock where ID=%s" % tpunch
            ct.execute(sql)
            action("%s Entered Manual Punch (%s)" % (cherrypy.session['user'], ref),rid)
        
        
     
        raise cherrypy.HTTPRedirect(dpath + "/manualpunch")
   
        
    @cherrypy.expose
    def manualpunch(self):
        if reqperm("Admin") != True:
            return reqperm("Admin")
            
        today = datetime.today()

        now = datetime.now()
        thisyear = now.year
        lastyear = now.year - 1

        lastmonth = today -  relativedelta(months=1)


        lm = lastmonth.strftime('%B')
        lmb = lastmonth.strftime('%m')
        tm = today.strftime('%B')
        tmb = today.strftime('%m')
        
        tinfo= [lm, lmb, tm, tmb, thisyear, lastyear]
        children = ct.execute('select children.name, children.ID , count(ctimeclock.plaindate) from children LEFT JOIN  ctimeclock on ctimeclock.childid=children.ID  WHERE children.active=1 group by children.ID order by count(ctimeclock.plaindate) desc, children.name asc')

        sql = 'SELECT ctimeclock.punchtime, ctimeclock.ref, children.name, ctimeclock.ID, children.ID from ctimeclock, children where ctimeclock.childid=children.ID and ctimeclock.punchtype="duration" order by ctimeclock.plaindate desc, ctimeclock.ref desc limit 500'
        infos = ct.execute(sql)
        
        
        mytemplate = Template(filename='templates/manualpunch.html', lookup=mylookup)
        lerender = mytemplate.render(tinfo=tinfo, children=children, infos=infos)
        return lerender   

    @cherrypy.expose
    def pmanualpunch(self):
        if reqperm("Admin") != True:
            return reqperm("Admin")
            
        today = datetime.today()

        lastmonth = today -  relativedelta(months=1)


        lm = lastmonth.strftime('%B')
        lmb = lastmonth.strftime('%m')
        tm = today.strftime('%B')
        tmb = today.strftime('%m')
        
        tinfo= [lm, lmb, tm, tmb]
        providers = ct.execute('select providers.name, providers.ID , count(ptimeclock.plaindate) from providers LEFT JOIN  ptimeclock on ptimeclock.provid=providers.ID group by providers.ID order by count(ptimeclock.plaindate) desc, providers.name asc')

        sql = 'SELECT ptimeclock.punchtime, ptimeclock.ref, providers.name, ptimeclock.ID, providers.ID from ptimeclock, providers where ptimeclock.provid=providers.ID and ptimeclock.punchtype="duration" order by ptimeclock.plaindate desc, ptimeclock.ref desc limit 200'
        infos = ct.execute(sql)
        
        
        mytemplate = Template(filename='templates/pmanualpunch.html', lookup=mylookup)
        lerender = mytemplate.render(tinfo=tinfo, providers=providers, infos=infos)
        return lerender   
        
        
        
    @cherrypy.expose
    def invrender(self,inv):
                
        checksession()
        
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        inv=int(inv)
        invoices = ct.execute('SELECT fname from invoices where ID="%s"' % inv)

        for invoice in invoices:
            inv = invoice[0]
            pdfpath = "invoice/%s" % inv
            buffer = StringIO.StringIO()        
            cherrypy.response.headers['Content-Type'] = "application/pdf"
            buffer.write(open(pdfpath, "rb").read())
            
            return buffer.getvalue()  
            
            
            
    @cherrypy.expose
    def adultremove(self, ID):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        ID= int(ID)
        sql = 'SELECT parent, childid, kind, approval from pperm where ID=%s' % ID
        infos = ct.execute(sql)
        infos = infos[0]
        parent = infos[0]
        childid = infos[1]
        kind = infos[2]
        if kind == "BParent":
            return rendererror("Unable to remove billing contact!")
        else:
            #Now look.
            sql = "SELECT name from adults where ID=%s" % parent
            parent = ct.execute(sql)
            pname = parent[0][0]
            
            sql = "SELECT name from children where ID=%s" % childid
            child = ct.execute(sql)
            cname = child[0][0]

            out = "%s has revoked permission for %s to %s" % (cherrypy.session['user'], pname, cname)
            action(out,"System")
            action(out, childid)
            
            sql = 'DELETE from pperm where ID=%s' % ID
            ct.execute(sql)
                
            return rendergeneric(out)


    @cherrypy.expose
    def cpunchremove(self,punchid):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        punchid = int(punchid)
        sql ="select childid from ctimeclock where ID=%s" % punchid
        x = ct.execute(sql)
        for xx in x:
            childid = xx[0]
            action("%s disabled punch ID %s" %(cherrypy.session['user'], punchid), childid)
            
            sql = "update ctimeclock set childid='REM:%s' where ID=%s" % (childid, punchid)
            ct.execute(sql)
        
        raise cherrypy.HTTPRedirect(dpath + "/manualpunch")            
    @cherrypy.expose
    def ppunchremove(self,punchid):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        punchid = int(punchid)
        sql ="select provid from ptimeclock where ID=%s" % punchid
        x = ct.execute(sql)
        for xx in x:
            childid = xx[0]
            action("%s disabled punch ID %s" %(cherrypy.session['user'], punchid), childid)
            
            sql = "update ptimeclock set provid='REM:%s' where ID=%s" % (childid, punchid)
            ct.execute(sql)
        
        raise cherrypy.HTTPRedirect(dpath + "/pmanualpunch")            
            
            
    @cherrypy.expose
    def ledgerremove(self,ID, rid, confirm=0):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        ID = int(ID)
        confirm = int(confirm)
        if confirm == 0:
            
            entry = ct.execute("select * from ledger where ID=%s" % ID)
            desc = "It looks like you are trying to remove ledger entry # %s, please confirm" % ID
            handler = "ledgerremove"
            args =[["rid",rid],["ID",ID]]
            return confirmaction(desc, handler, args, entry)
        else:
            sql = "update ledger set childid='disabled' where ID=%s" % ID
            ct.execute(sql)
            
            
            action("%s disabled ledger ID %s for child %s" %(cherrypy.session['user'], ID, rid), rid)

            raise cherrypy.HTTPRedirect(dpath + "/ledger?rid=%s" % rid)                
    @cherrypy.expose
    def pl(self, offset=0):
        offset=int(offset)
        tstart = time.time()
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")
        
        today = datetime.today()
        monthbegin = datetime(today.year, today.month, 1) + relativedelta(months=int(offset))

        
        thismonth = monthbegin.strftime('%B')
        
        #monthbegin = monthbegin -  relativedelta(months=1)
        
        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
        
        sql  = 'SELECT children.name, children.status, children.ID, children.dob, children.restrictions, children.is4cs, children.rate from  children, pperm, adults where pperm.parent=adults.ID AND pperm.childid=children.ID AND pperm.kind="BParent" AND children.active=1 '
        print sql
        cinfo = ct.execute(sql)
        children = []

        totalrev = 0

        for c in cinfo:
            
            cage =   c[3]          
            is4cs = c[5]

            try:
                rover = float(c[6])
            except:
                rover
            timeinfo, ctotal = docalcuations(c[2], cage, is4cs, monthbegin,nextmonth,0,rover)
            #timeinfo, ctotal = docalcuations(c[2], cage, is4cs, lastmonth, monthbegin,0)
            ctotal = round(ctotal,2)
            totalrev = totalrev + ctotal
            ctotal = "%.2f" %  ctotal
            child= [c[0],c[1],c[2],c[3],c[4], timeinfo, ctotal]
            children.append(child)
            


            
        totallabor = 0
        totallhours = 0.0
        providers = []
        provider = ct.execute('select ID, name, phone, rate from providers')
        for p in provider:
            pid = p[0]
            rate = p[3]
            name = p[1]
            
            timeinfo, ptotal, totalh = dotimeclockcalcsprovider(pid, rate, monthbegin, nextmonth)
            ptotal = round(ptotal, 2)
            totallhours = totallhours + totalh
            totallabor = totallabor + ptotal
            
            providers.append([name, ptotal, pid])

        dtmin=monthbegin
        dtmax=nextmonth
        
        desclike = 'desc like "Payment by Barter%" or desc like "Payment by Credit%" or desc like "Payment by Write Off %" '
        #sql = 'SELECT ID, childid, desc, amt from ledger where dt >= "%s" and dt < "%s" and childid != "disabled"  and (%s) order by dt asc' % (dtmin, dtmax,desclike)
        sql = 'SELECT sum(amt) from ledger where dt >= "%s" and dt < "%s" and childid != "disabled"  and (%s) order by dt asc' % (dtmin, dtmax,desclike)
        ledger = ct.execute(sql)
        chargeoff = ledger[0][0]
        
        try:
            chargeoff = chargeoff * -1
        except:
            chargeoff = 0
            pass


        netincome = totalrev - totallabor - chargeoff
        
        netincome = "%.2f" %  netincome       
        
        totallabor= "%.2f" %  totallabor            
        totalrev = "%.2f" %  totalrev            
        chargeoff = "%.2f" %  chargeoff            
        mytemplate = Template(filename='templates/pl.html', lookup=mylookup)

        offsets = [offset, offset+1, offset-1, thismonth]
        #       render duration, servername, currenttime
        diag = [time.time() - tstart, servername, datetime.now()]

        laborinfo = ["%.2f" % totallhours, totallabor]
        lerender = mytemplate.render(laborinfo=laborinfo, children=children, totalrev=totalrev, diag=diag, providers=providers,totallabor=totallabor, netincome=netincome, chargeoff=chargeoff, offsets=offsets)
        return lerender

        
    @cherrypy.expose
    def ledger(self, rid, amt=0, ptype='', **kwargs):
        if reqperm("Admin") != True:
            return reqperm("Admin")        
        try:
            amt = float(amt)
        except:
           return rendererror("The amount field should contain a number only!")     
        
        try:
            child = ct.execute('select name, dob, photo, ID from children  where ID=%s' % rid)
            child = child[0]
        except:
            return rendererror("Child ID does not exist..")

        today = datetime.today()
        now = today.strftime('%Y-%m-%d %H:%M:%S')
        
        
        
        
        lastyear = today -  relativedelta(years=1)


        ly = lastyear.strftime('%Y')
        lyb = lastyear.strftime('%y')
        ty = today.strftime('%Y')
        tyb = today.strftime('%y')
        
        thismonth = today.strftime('%m')
        thisday = today.strftime('%d')
        
        tinfo= [ly, lyb, ty, tyb, thismonth, thisday]        
        
        
        
        
        
        monthbegin = datetime(today.year, today.month, 1)

        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
        
        if amt > 0:
            #add a new entry
            day = kwargs['day']
            month = kwargs['month']
            year = kwargs['year']
            notes = kwargs['notes']
            ltype = kwargs['ltype']
            
            
            dt = "%s-%s-%s" % (year, month, day)
            dt = datetime.strptime(dt, '%Y-%m-%d')
            dt = dt.strftime('%Y-%m-%d' )

            if ltype == "PMT":
                desc = "Payment by %s" % ptype
                desc = "%s \n%s" %(desc, notes)
                ledgeraddpayment(rid, amt, desc, dt)
                action("User %s recorded a payment of $%s on the account of child %s" %(cherrypy.session['user'], amt, rid), "System")

            else:

                desc = "%s " %(notes)
                ledgeraddcharge(rid, amt, desc, dt)
                action("User %s recorded a charge of $%s on the account of child %s" %(cherrypy.session['user'], amt, rid), "System")

            #ct.execute('insert into ledger (dt, childid, desc, amt) values ("%s","%s","%s", %s)' % (dt, rid, desc, amt))
            raise cherrypy.HTTPRedirect(dpath + "/ledger?rid=%s" % rid)            

        
        ledger = ct.execute('select dt, desc, amt, ID from ledger where childid="%s" order by dt asc' % rid)
       
        total = ct.execute('select sum(amt) from ledger where childid="%s" group by childid' % rid)
        #total = total[0]
        
        
        mytemplate = Template(filename='templates/ledger.html', lookup=mylookup)
        lerender = mytemplate.render(ledger=ledger, total=total, rid=rid, child=child, tinfo=tinfo)
        return lerender
    @cherrypy.expose
    def me(self):
        checksession()
        
        
        name = cherrypy.session['user'] 
        idd = cherrypy.session['userid']

        info = ct.execute("select name, key from users where ID=%s" % idd)
        info = info[0]

        key = info[1]
        totp = pyotp.TOTP(key)
        
        currentotp = totp.now()

        
        mytemplate = Template(filename='templates/me.html', lookup=mylookup)
        lerender = mytemplate.render(name=name, info=info, currentotp=currentotp, session=cherrypy.session, key=key)
        return lerender

    @cherrypy.expose
    def myid(self,rid):
        checksession()
        name = cherrypy.session['user'] 
        if cherrypy.session['userid'] == rid:
            rid=int(rid)
            info = ct.execute("select name, key from users where ID=%s" % rid)
            info = info[0]
    
            key = info[1]
          
            keyuri = pyotp.totp.TOTP(key).provisioning_uri(name, issuer_name="Legion Daycare")
            img = qrcode.make(keyuri)
            cherrypy.response.headers['Content-Type'] = "image/png"
            buffer = StringIO.StringIO()
            img.save(buffer, 'PNG')
            return buffer.getvalue()
            
        else:
            return 'oops'
    @cherrypy.expose
    def invoiceall(self, offset=0 ):
        offset = int(offset)
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")
        
        epoch_time = int(time.time())
        name = "%s-pdfdump.pdf" % (epoch_time)
        task = BackgroundTask(interval = 0, function = invall, args = [name, offset], bus = cherrypy.engine)
        task.args.insert(0, task)
        task.start()
        
        #os.remove("res/pdfdump.pdf")
        
        return "Requested! please check <a href='res/%s'>here</a> after a bit.." % name
#        merger.write("pdfdump.pdf")
#        buffer = StringIO.StringIO()        
#        cherrypy.response.headers['Content-Type'] = "application/pdf"
#        buffer.write(open("pdfdump.pdf", "rb").read())
#        return buffer.getvalue()
    @cherrypy.expose
    def downgrade(self):
        checksession()
        cherrypy.session['level'] = "clockonly"
        
        info = "Your session has been downgraded to 'clockonly' and can only operate the timeclock. To resume administrative capability please log in again (after logging out)"
        raise cherrypy.HTTPRedirect(dpath + "/checkin")  
        
    @cherrypy.expose
    def ledgermain(self):
        checksession()
        if reqperm("Admin") != True:
            return reqperm("Admin")            
        
        test=[]
        adults = "select adults.name, adults.ID from adults order by adults.name asc"
        adults = ct.execute(adults)
        for adult in adults:
            aid = adult[1]
            aname = adult[0]
            pperms = 'select pperm.childid, children.name from  pperm, children where pperm.childid = children.ID and pperm.kind="BParent" and pperm.parent=%s' % aid
            pperms = ct.execute(pperms)
            
            atotal=0
            cinfo = []
            for ppm in pperms:
                cid = ppm[0]
                cname = ppm[1]
                #we have a child entry, its okay for this parent to be added
                ledger = 'select sum(amt) from ledger where childid="%s"' % cid
                ledger = ct.execute(ledger)
                lval = ledger[0][0]
                
                if lval != None:
                    atotal = atotal + float(lval)
                cinfo.append([cname, cid, lval])
                
                
                
            if len(cinfo) > 0:
                test.append([aname, cinfo, atotal])
        
            #pull together
        
        test = sorted(test,key=lambda x: x[2], reverse=True)
        
        mytemplate = Template(filename='templates/ledgermain.html', lookup=mylookup)
        lerender = mytemplate.render(test=test)
        return lerender  
    
    @cherrypy.expose
    def providermagic(self, key):
        
        
        magic = "3400"
        
        if key == str(magic):
            cherrypy.session['active'] = True
            cherrypy.session['user'] = "Provider"
            cherrypy.session['userid'] = "Provider x"        
            cherrypy.session['level'] = "clockonly"
    
            
            raise cherrypy.HTTPRedirect(dpath + "/checkin")      
        else:
            raise cherrypy.HTTPRedirect(dpath + "/login")      
    
    
pdflist =[]
def invall(task, name, offset=0):
    global pdflist
    pdflist = []
    adj=offset+1
    sql  = 'SELECT ID from  children where active=1'
    cinfo = ct.execute(sql)
  
    for c in cinfo:        
        a = {"c": c, 'adj': adj}
        bulkq.put(a)
        
    bulkq.join()
    
    merger = PdfFileMerger()    
    for pdfpath in pdflist:
       merger.append(PdfFileReader(file(pdfpath, 'rb')))
    merger.write("res/%s" % name)

    
    task.cancel()
        
        
def photoupload(photo,target):
    all_data = bytearray()
    while True:
        data = photo.file.read(8192)
        all_data += data
        if not data:
            break       
    
    tempfile = os.path.join(os.path.dirname(os.path.realpath(__file__)), "temp")
    tempfile = os.path.join(tempfile,(photo.filename))
    print tempfile
    
    saved_file=open(tempfile, 'wb') 
    saved_file.write(all_data) 
    saved_file.close()
    print     
    im = Image.open(tempfile)    
        
    im.thumbnail((300,300))
    im.save(target,'JPEG')
    os.remove(tempfile)
                
                

def checksession():
    if 'active' not in cherrypy.session:
        # redirect to login screen
        print "Session var not set ", cherrypy.session
        raise cherrypy.HTTPRedirect(dpath + "/login")
    elif cherrypy.session['active'] == False:
        # redirect to login screen
        print "Session var set, active=", cherrypy.session['active']
        raise cherrypy.HTTPRedirect(dpath + "/login")
    else:
        return True
    

def ploginsession():
    if 'pactive' not in cherrypy.session:
        print 'pactive not in session, aborting' 
        cherrypy.session['pactive'] == False
        raise cherrypy.HTTPRedirect(dpath + "/parentcheckin")
    elif cherrypy.session['pactive'] == False:
        # redirect to login screen
        print 'pactive false, aborting' 

        raise cherrypy.HTTPRedirect(dpath + "/parentcheckin")
    else:
        print 'pactive true continue' 

        return True    


def action(action,ref):
        now = datetime.now()
        now = now.strftime('%Y-%m-%d %I:%M:%S %p')
        ct.execute('insert into activity (kind, time, ref) values ("%s","%s","%s")' % (action,now,ref))

#c.execute('create table if not exists ctimeclock ( ID INTEGER PRIMARY KEY AUTOINCREMENT, punchtype, punchtime, childid)')

def clockin(cid):
        now = datetime.now()
        nowa = now
        dt = now.strftime('%Y-%m-%d %H:%M:%S')

        now = now.strftime('%Y-%m-%d %I:%M:%S %p')
        ct.execute('insert into ctimeclock (punchtype, punchtime, childid, dt, plaindate) values ("in","%s","%s","%s","%s")' % (now,cid,dt,nowa.date()))
        
        
def clockout(cid):
        nowa = datetime.now()
        now = nowa.strftime('%Y-%m-%d %I:%M:%S %p')
        dt = nowa.strftime('%Y-%m-%d %H:%M:%S')
        
        ct.execute('insert into ctimeclock (punchtype, punchtime, childid, dt, plaindate) values ("out","%s","%s","%s","%s")' % (now,cid,dt, nowa.date()))

        
        #get last clockin..
        sql = 'select punchtype, punchtime from ctimeclock where childid= "%s" and punchtype="in" order by ID desc LIMIT 1' % (cid)
        print sql
        cin = ct.execute(sql)
        try:
            cin = cin[0]
            print cin
            ptime = cin[1]
            clockin = datetime.strptime(ptime, '%Y-%m-%d %I:%M:%S %p')
            duration = nowa - clockin
            duration = duration.total_seconds()
            print duration
            durhours = duration/3600
        
            ref = "%s to %s" % (ptime, now)
        
            #ct.execute('insert into ctimeclock (punchtype, punchtime, childid, dt, ref, plaindate) values ("duration","%s","%s","%s","%s","%s")' % (durhours,cid,now,ref,nowa.date()))
            ct.execute('insert into ctimeclock (punchtype, punchtime, childid, dt, ref, plaindate) values ("duration","%s","%s","%s","%s","%s")' % (durhours,cid,dt,ref,nowa.date()))
        
        except:
            action('Error in punchout - unable to find punch in!',cid)
            pass
        
def pclockin(cid):
        now = datetime.now()
        nowa = now
        dt = now.strftime('%Y-%m-%d %H:%M:%S')

        now = now.strftime('%Y-%m-%d %I:%M:%S %p')
        ct.execute('insert into ptimeclock (punchtype, punchtime, provid, dt, plaindate) values ("in","%s","%s","%s","%s")' % (now,cid,dt,nowa.date()))
        
        
def pclockout(cid):
        nowa = datetime.now()
        now = nowa.strftime('%Y-%m-%d %I:%M:%S %p')
        dt = nowa.strftime('%Y-%m-%d %H:%M:%S')
        
        ct.execute('insert into ptimeclock (punchtype, punchtime, provid, dt, plaindate) values ("out","%s","%s","%s","%s")' % (now,cid,dt, nowa.date()))

        
        #get last clockin..
        sql = 'select punchtype, punchtime from ptimeclock where provid= "%s" and punchtype="in" order by ID desc LIMIT 1' % (cid)
        print sql
        cin = ct.execute(sql)
        try:
            cin = cin[0]
            print cin
            ptime = cin[1]
            clockin = datetime.strptime(ptime, '%Y-%m-%d %I:%M:%S %p')
            duration = nowa - clockin
            duration = duration.total_seconds()
            print duration
            durhours = duration/3600
        
            ref = "%s to %s" % (ptime, now)
        
            #ct.execute('insert into ctimeclock (punchtype, punchtime, childid, dt, ref, plaindate) values ("duration","%s","%s","%s","%s","%s")' % (durhours,cid,now,ref,nowa.date()))
            ct.execute('insert into ptimeclock (punchtype, punchtime, provid, dt, ref, plaindate) values ("duration","%s","%s","%s","%s","%s")' % (durhours,cid,dt,ref,nowa.date()))
        
        except:
            action('Error in punchout - unable to find punch in!',cid)
            pass
        
def getchildmonths(dob):

        dob = datetime.strptime(dob, '%Y-%m-%d')
        now = datetime.now()
        
        months = (now.year - dob.year) * 12 + now.month - dob.month
        
        print 'Child is ' ,months    ,' months old'
        return months
        
        
def docalcuations(cid, dob, is4cs, dtmin,dtmax,concat=0, rateoverride=0):
    months = getchildmonths(dob)   

    
    if months < 24:
        rate = infrate
    else:
        rate = chrate
    if is4cs==1:
        rate = r4cs
        concat = 1
        
    visa=''                
    if rateoverride > 0:
        rate = float(rateoverride)
        visa='*'
    #default SQL, returns all 'duration' entries
    sql = 'SELECT punchtime, dt, ref from ctimeclock where punchtype = "duration" and childid = "%s" and dt >= "%s" and dt < "%s" order by plaindate asc' % (cid, dtmin, dtmax)
    if concat == 1:
        #Simplified, if there are more than one "duration" entry return the sum of the punchtimes by grouping on the plaindate field.
        sql = 'SELECT sum(punchtime), dt, plaindate from ctimeclock where punchtype = "duration" and childid = "%s" and dt >= "%s" and dt < "%s" group by plaindate order by plaindate asc' % (cid, dtmin, dtmax)
       # dtmin = "2017-07-31"
       # dtmax = "2017-09-01"
    timeinfo=[]
    ctotal = 0
    #sql = 'SELECT punchtime, dt, ref from ctimeclock where punchtype = "duration" and childid = "%s" and dt >= "%s" and dt < "%s"' % (cid, dtmin, dtmax)
    print sql
    time = ct.execute(sql)
    for t in time:
        hours = float(t[0])
        cost = rate * hours
        costs=''
        costs = "%.2f" % cost
        print costs
        if is4cs==1:
            #check for maximums
            if months < 24:
                print('max is 26.53/day')
                if cost > 26.53:
                    cost = 26.53
                    costs = '26.53*'
            else:
                print('max is 23.98/day')
                if cost > 23.98:
                    cost = 23.98
                    costs = '23.98*'
    
            
    
            
        ctotal = ctotal + cost
    
        hours =    "%.3f" %  hours
    
        ref = t[2]            
        ttime = [ref,hours,rate, costs ]
        timeinfo.append(ttime)
    return timeinfo, ctotal
        
def dotimeclockcalcsprovider(pid, rate, dtmin, dtmax,concat=0):
    rate = float(rate)
    sql = 'SELECT punchtime, dt, ref, ID from ptimeclock where punchtype = "duration" and provid = "%s" and dt >= "%s" and dt < "%s" order by plaindate asc' % (pid, dtmin, dtmax)
    if concat == 1:   
        sql = 'SELECT sum(punchtime), dt, plaindate from ptimeclock where punchtype = "duration" and provid = "%s" and dt >= "%s" and dt < "%s" group by plaindate order by plaindate asc' % (pid, dtmin, dtmax)
    timeinfo=[]
    ctotal = 0
    print sql
    totalh=0.00
    time = ct.execute(sql)
    for t in time:
        hours = float(t[0])
        totalh = totalh +  hours
        cost = rate * hours
        costs=''
        costs = "%.3f" % cost
        ctotal = ctotal + cost
        hours =    "%.3f" %  hours
        ref = t[2]            
        ttime = [ref,hours,rate, costs, t[3] ]
        timeinfo.append(ttime)
    return timeinfo, ctotal, totalh
    
def doclean():
    print 'clean'
    
    
def rendererror(error):
    mytemplate = Template(filename='templates/error.html', lookup=mylookup)
    lerender = mytemplate.render(info=error)
    return lerender

def reqperm(perm):
    if cherrypy.session['level'] != perm:
        oops = """Sorry, you do not have permission to view this page.. <br>
        If you believe this to be an error, please try logging out and logging in again. <br>Please note that this site requires cookies to function.
        <br>
        Your permission level is "%s"
        """ % cherrypy.session['level']
        return rendererror(oops)
    else:
        return True
    
def rendergeneric(info):
    mytemplate = Template(filename='templates/generic.html', lookup=mylookup)
    lerender = mytemplate.render(info=info)
   
    return lerender


    
    
def degunk(kwargs,item,default):
    
    thing = kwargs.get(item,default)  
    thing = str(thing)
    thing = thing.replace("'", '')
    thing = thing.replace('"', '')
    return thing
    
def ledgeraddinvoice(rid, month, ctotal, monthbegin):
    now = monthbegin.strftime('%Y-%m-%d' )
    print 'ledgeraddinvoice', month, rid, ctotal
    
    desc = "%s Tuition" % month
    ct.execute('delete from ledger where childid="%s" and desc="%s"' % (rid, desc))
    ct.execute('insert into ledger (dt, childid, desc, amt) values ("%s","%s","%s", %s)' % (now, rid, desc, ctotal))


def ledgeraddpayment(rid, pmt, desc, dt):
    pmt = float(pmt) * -1
    pmt = "%.2f" %  pmt
    ct.execute('insert into ledger (dt, childid, desc, amt) values ("%s","%s","%s", %s)' % (dt, rid, desc, pmt))
    
    
    
def ledgeraddcharge(rid, pmt, desc, dt):
    pmt = float(pmt) 
    pmt = "%.2f" %  pmt
    ct.execute('insert into ledger (dt, childid, desc, amt) values ("%s","%s","%s", %s)' % (dt, rid, desc, pmt))

    
    
def invoiceaddupdate(pdfname, rid, ctotal):
    
    now = datetime.now()
    cnt = ct.execute('select count(*) from invoices where fname="%s"' % pdfname )
    cnt = cnt[0][0]
    if cnt > 0:
        sql = 'update invoices set dt="%s" where fname="%s"' % (now, pdfname)
    else:
        sql = 'insert into invoices (dt, childid, fname, amt) values ("%s","%s","%s","%s")' % (now, rid, pdfname,ctotal)
    ct.execute(sql)            
    
    #now get the id from the invoice table..
    sql = "SELECT ID from invoices where dt='%s'" % now
    leid = ct.execute(sql)
    leid= leid[0][0]
    return leid
    
def renderledger(cid, dtmin, dtmax):
    
    
    dtmax = dtmax + relativedelta(months=1)
    ledger=[]
    ##balance forward
    sql = 'SELECT sum(amt) from ledger where childid = "%s" and dt < "%s" order by dt asc' % (cid, dtmin)
    print(sql)
    merp = ct.execute(sql)
    
    
    rtotal = 0
    bf = merp[0][0]
    if bf == None:
        bf = 0
        
    else:
            
        print bf
        desc = "Balance Forward"
        dt = " "
        amt = bf
        rtotal = bf
        if amt > 0:
            
            linfo = [dt, desc, "","\$%.2f" % bf]
        else:
            bf= bf * -1
            linfo = [dt, desc,"\$%.2f" % bf, ""]
        ledger.append(linfo)
        
    

    ##current detail
    sql = 'SELECT dt, desc, amt from ledger where childid = "%s" and dt >= "%s" and dt < "%s" order by dt asc' % (cid, dtmin, dtmax)
    print(sql)
    merp = ct.execute(sql)
    for m in merp:
        dt = m[0]
        desc = m[1]

        desc = desc.replace("#","\#")
        desc = desc.replace("\n", "\\newline ")

        amt = m[2]
        rtotal = rtotal + amt
        if amt >= 0:
            linfo = [dt, desc, "","\$%.2f" % amt, rtotal]
        else:
            amt = amt*-1
            linfo = [dt, desc, "\$%.2f" % amt, "", rtotal]
        ledger.append(linfo)
        
    
    ##current balance
    sql = 'SELECT sum(amt) from ledger where childid = "%s" ' % (cid, )
    print(sql)
    merp = ct.execute(sql)
    
    bf = merp[0][0]
    if bf == None:
        bf = 0
                
    dt = " "
    desc = "Current Balance"
    amt = bf
    if amt > 0:
        linfo = [dt, desc, "", "\$%.2f" % amt]
    else:
        if amt == 0:
            amt = amt * 1
        else:
            amt = amt * -1
        linfo = [dt, desc, "\$%.2f" % amt, ""]

        
        
    return ledger, linfo

def confirmaction(desc, handler, args, entry=[]):
    tstart = time.time()
    diag = [time.time() - tstart, servername, datetime.now()]
    mytemplate = Template(filename='templates/confirmaction.html', lookup=mylookup)
    lerender = mytemplate.render(diag=diag, desc=desc, handler=handler, args=args, entry=entry)
    return lerender

def buildpdf(rid, month, ctotal, monthbegin, lastmonth, child, pinfo, adj, c):

   
    

    texname = "%s Invoice %s.tex" % (month, c[0])
    texpath = 'invoice/%s' % texname
    pdfname = "%s Invoice %s.pdf" % (month, c[0])
    pdfpath = 'invoice/%s' % pdfname
    
    
    invid = invoiceaddupdate(pdfname, rid, ctotal)
    
    ledger, linfo = renderledger(rid, lastmonth,monthbegin)
    
    invid = str(invid)
    while len(invid) < 5:
        invid = "0%s" % invid
    mytemplate = Template(filename='templates/invoicetex.html', lookup=mylookup)
    lerender = mytemplate.render(c=child, lm=lastmonth.date(), mb=monthbegin.date(), p1=pinfo, adj=adj, month=month, rid=rid, invid=invid, ledger=ledger, linfo=linfo)
    
    #take the rendered template and save it
    with open(texpath, 'w') as f:
        f.write(lerender)
    
    proc = subprocess.Popen('pdflatex "%s"' % (texname) ,shell=True, cwd="invoice")
    tick=0
    while proc.poll() == None:
        time.sleep(1)
        tick=tick+1
        if tick > 10:
            proc.terminate()
            print('Terminating Process')
            return rendererror("PDf generation process ran too long, the system may be overloaded or there may be some illegal characters (#,$,\,etc) in the render see below <br><pre>%s</pre>" % lerender)
    
    
    buffer = StringIO.StringIO()        
    cherrypy.response.headers['Content-Type'] = "application/pdf"
    buffer.write(open(pdfpath, "rb").read())
        

    return buffer.getvalue()      
    
def bufferpdf(rid, month, ctotal, monthbegin, lastmonth, child, pinfo, adj, c):

   
    

    texname = "%s Invoice %s.tex" % (month, c[0])
    texpath = 'invoice/%s' % texname
    pdfname = "%s Invoice %s.pdf" % (month, c[0])
    pdfpath = 'invoice/%s' % pdfname
    
    
    invid = invoiceaddupdate(pdfname, rid, ctotal)
    
    ledger, linfo = renderledger(rid, lastmonth,monthbegin)
    
    invid = str(invid)
    while len(invid) < 5:
        invid = "0%s" % invid
    mytemplate = Template(filename='templates/invoicetex.html', lookup=mylookup)
    lerender = mytemplate.render(c=child, lm=lastmonth.date(), mb=monthbegin.date(), p1=pinfo, adj=adj, month=month, rid=rid, invid=invid, ledger=ledger, linfo=linfo)
    
    #take the rendered template and save it
    with open(texpath, 'w') as f:
        f.write(lerender)
    
    proc = subprocess.Popen('pdflatex "%s"' % (texname) ,shell=True, cwd="invoice")
    tick=0
    while proc.poll() == None:
        time.sleep(1)
        tick=tick+1
        if tick > 10:
            proc.terminate()
            print('Terminating Process')
            return rendererror("PDf generation process ran too long, the system may be overloaded or there may be some illegal characters (#,$,\,etc) in the render see below <br><pre>%s</pre>" % lerender)
    
    return pdfpath    
    
    
def checkpuncherrors():
    
    #check if somebody has been in too long!
    nowa = datetime.now()
    errors = []
    errorcheck = ct.execute('SELECT children.name, children.ID, children.photo from  children where children.active=1 and children.status ')
    for child in errorcheck:
        sql = 'select punchtype, punchtime from ctimeclock where childid= "%s" and punchtype="in" order by ID desc LIMIT 1' % (child[1])
        print sql
        cin = ct.execute(sql)
        try:
            cin = cin[0]
            ptime = cin[1]
            cintime = datetime.strptime(ptime, '%Y-%m-%d %I:%M:%S %p')
            duration = nowa - cintime
            duration = duration.total_seconds()
            durhours = duration/3600
            
            
            if durhours > 12:
                errors.append("%s has been clocked in for %s hours!!" % (child[0], int(durhours) ))
        except:
            pass
    return errors
def check_employee_timeerrors():
    
    errors = []

    #sql = 'SELECT ptimeclock.punchtime, ptimeclock.dt, ptimeclock.ref, ptimeclock.ID, providers.name from ptimeclock, providers where providers.ID = ptimeclock.provid and punchtype = "duration" and  dt >= "%s" and dt < "%s" order by plaindate asc' % ( dtmin, dtmax)
    sql = 'SELECT ptimeclock.punchtime, ptimeclock.dt, ptimeclock.ref, ptimeclock.ID, providers.name from ptimeclock, providers where providers.ID = ptimeclock.provid and punchtype = "duration" '
    time = ct.execute(sql)
    for t in time:
        hours = float(t[0])
        if hours > 12:
            errors.append("Employee punched in too long: %s at %s for %.2f hours!" % (t[4], t[1], hours) )
        if hours < 0.05:
            errors.append("Employee short punched: %s at %s" % (t[4], t[1]))

    return errors
def check_child_timeerrors():
    today = datetime.today()

    monthbegin = datetime(today.year, today.month, 1) 

    lastmonth = monthbegin -  relativedelta(months=1)
    nextmonth = monthbegin +  relativedelta(months=1)

    dtmin = lastmonth
    dtmax = nextmonth

    errors = []

    #sql = 'SELECT ptimeclock.punchtime, ptimeclock.dt, ptimeclock.ref, ptimeclock.ID, providers.name from ptimeclock, providers where providers.ID = ptimeclock.provid and punchtype = "duration" and  dt >= "%s" and dt < "%s" order by plaindate asc' % ( dtmin, dtmax)
    sql = 'SELECT ctimeclock.punchtime, ctimeclock.dt, ctimeclock.ref, ctimeclock.ID, children.name from ctimeclock, children where children.ID = ctimeclock.childid and punchtype = "duration" and  dt >= "%s" and dt < "%s" order by ctimeclock.ID desc' % ( dtmin, dtmax)
    time = ct.execute(sql)
    for t in time:
        hours = float(t[0])
        if hours > 12:
            errors.append("Child punched in too long: %s at %s for %.2f hours!" % (t[4], t[1], hours) )
        if hours < 0.05:
            errors.append("Child short punched: %s at %s" % (t[4], t[1]))

    return errors




def texproc(q):
    global pdflist
    
    while True:
        info = q.get()
        c = info['c']
        adj = info['adj']
        rid = c[0]
        today = datetime.today()
        monthbegin = datetime(today.year, today.month, 1) + relativedelta(months=adj)

        lastmonth = monthbegin -  relativedelta(months=1)
        nextmonth = monthbegin +  relativedelta(months=1)
        print 'xxxx', lastmonth, monthbegin, nextmonth      
        
        cinfo = ct.execute('SELECT children.name, children.status, children.ID, children.dob, children.restrictions, children.is4cs, children.rate from  children where children.ID="%s"' % rid)
        c = cinfo[0]
        pinfo = ct.execute('SELECT adults.name, adults.address, adults.phone, pperm.kind from adults, children,  pperm WHERE adults.ID = pperm.parent AND pperm.childid=children.ID  AND pperm.kind="BParent" AND children.ID=%s' % rid)

        cage =   c[3]          
        is4cs = c[5]
        timeinfo, ctotal = docalcuations(c[2], cage, is4cs, lastmonth,monthbegin,0,float(c[6]))
        ctotal = "%.2f" %  ctotal
        child= [c[0],c[1],c[2],c[3],c[4], timeinfo, ctotal]
        month = lastmonth.strftime('%B %Y')
        

        

        #take invoice amounts and enter into ledger
        ledgeraddinvoice(rid, month, ctotal, monthbegin)
        #build pdf using latex and return to the session
        pdfpath = bufferpdf(rid, month, ctotal, monthbegin, lastmonth, child, pinfo, adj, c)
        pdflist.append(pdfpath)
        
        q.task_done()

bulkq = Queue(maxsize=0)
#num_threads = 1
for i in range(0,5):
    worker = Thread(target=texproc, args=(bulkq,))
    worker.setDaemon(True)
    worker.start()




if __name__ == '__main__':

    cherrypy.engine.cleanup = cherrypy.process.plugins.BackgroundTask(3600*24, doclean)
    cherrypy.engine.cleanup.start()
    
    cherrypy.quickstart(StringGenerator(),base, config='server.config')
        
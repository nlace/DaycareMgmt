# WIP

This project was started as a challenge to use a sqlite database for a small production application (1-2) users. 


# Installation and setup

Get the code from backup file 


## Install basic dependencies (python 2.7 is included with most distributions at the time of this writing)

sudo apt install texlive-full python-pip
sudo pip -i requirements.txt


## Configure

Edit server.config to show the full paths of the folder you picked for the software.


## Test

python daycare.py

If there are no errors, the daycare manager is running and can be accessed from the servers IP address (you should make it static on your DHCP server)


## Setup SYSTEMCTL


Create a service to launch the daycare manager at startup



## Backups

Add cron job to backup the daycare folder

Update the dobackup.sh script to show the path you have chosen for your software and backups

sudo apt install p7zip-full

## Limited Login

Prior to transitioning the software from internet to intranet there was functionality to establish a limited user login using patterns.
This has been recoded to only require the "key" parameter to be set to "3400".

10.0.0.29:8087/providermagic?key=3400
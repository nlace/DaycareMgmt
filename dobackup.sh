#!/bin/bash

cd /home/daycare

DATE=`date +%Y.%m.%d.%H.%M.%S`
echo $DATE
echo $lesizefile

BWEBAPP="/home/daycare/Desktop/backups"
mkdir $BWEBAPP

BWEBDate="$BWEBAPP/$DATE"
#mkdir $BWEBDate



#cd /backup/$DATE



#copy legiondaycare webapp to zip file for backup
7za a -mx3 -tzip $BWEBDate-legiondaycare.7z /home/daycare/Desktop/daycare > /dev/null
7za a -mx3 -tzip -pmegaman $BWEBDate-legiondaycaredb.7z /home/daycare/Desktop/daycare/stuff.db > /dev/null




# remove extra backups, but keep at least 5, 1db plus 1full per backup = 10
bkups=10
echo keeping last $bkups backup files..

bkups=$(($bkups + 1))

pushd "$BWEBAPP"
ls -t1 "$BWEBAPP" | tail -n +$bkups | xargs rm
popd


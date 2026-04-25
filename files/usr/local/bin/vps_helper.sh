#!/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# vim: filetype=bash

find_orphaned_socks_servers() {
local SOCKS_PORT
local SS=$(mktemp)
ss -tnlp > $SS 
for SOCKS_PORT in $( systemctl list-units| grep 'gost_quic_server@.* running'| awk '{print $1}'| cut -d@ -f2| cut -d.  -f1  )
do
    if grep -q "127.0.0.1:$SOCKS_PORT " $SS
    then
        echo "= SOCKS_PORT $SOCKS_PORT live" 1>&2
    else
        echo "= SOCKS_PORT $SOCKS_PORT orphaned" 1>&2
        echo $SOCKS_PORT
    fi
done
rm $SS -f 
}

purge_orphaned_socks_servers() {
local O=$(mktemp)
local i
local SL=10
local max_attempts=5
local SOCKS_PORT
local num_occurences
for i in `seq $max_attempts`
do
    echo = getting orphaned SOCKS servers attempt $i/$max_attempts
    find_orphaned_socks_servers >> $O
    if [[ $i != $max_attempts ]]
    then
        echo = sleep $SL
        sleep $SL
    fi
done
local some_stopped=0
local down_occurences
echo "= getting info done. now decide if a port need to be stopped"
for SOCKS_PORT in $( cat $O| sort| uniq) 
do
    down_occurences=$(grep "^$SOCKS_PORT$" $O|wc -l)
    if  [[ $down_occurences == $max_attempts ]]
    then
        echo "= stop SOCKS_PORT $SOCKS_PORT because it was down $max_attempts times"
        systemctl stop gost_quic_server@$SOCKS_PORT
        rm -f /run/systemd/system/gost_quic_server@$SOCKS_PORT.service
        some_stopped=1
    else
        echo "= SOCKS_PORT $SOCKS_PORT was down $down_occurences times, won't stop it"
    fi
done
if [[ $some_stopped == 0 ]]
then
    echo "= none were stopped"
else
    systemctl daemon-reload
fi
}

usage(){
echo USAGE
echo $0 purge_orphaned_socks_servers
}

case $1 in
purge_orphaned_socks_servers)       purge_orphaned_socks_servers    ;;
*)  usage   ;;
esac



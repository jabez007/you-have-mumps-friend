YHMFBENCH ; SPDX-License-Identifier: GPL-3.0-or-later
 quit
run(warehouse,lotprefix,product,iterations,workload)
 new index,reply,samples,operation,started
 for index=1:1:3 do cycle(warehouse,lotprefix,product,0,workload,0)
 write "ready",!
 read reply
 if reply'="go" set $ecode=",U2001,"
 set %YHMFHashCalls=0,%YHMFAttempts=0
 set started=$zut
 for index=1:1:iterations do cycle(warehouse,lotprefix,product,index,workload,1)
 write "elapsed|",($zut-started),!
 set operation=""
 for  set operation=$order(samples(operation)) quit:operation=""  do
 . for index=1:1:iterations write "sample|",operation,"|",samples(operation,index),!
 write "cache|",$get(%YHMFDigestCache("count"),0),!
 quit
cycle(warehouse,lotprefix,product,index,workload,measured)
 new path,input,lot,status,started,hashes,attempts
 set lot=lotprefix_$select(workload="churn":index,1:0)
 set path(1)="locations",path(2)="DOCK",path(3)="bins",path(4)="B1",path(5)="balances"
 set input("id")=lot,input("lot")=lot,input("product")=product,input("quantity")="1"
 if workload="cold" kill %YHMFDigestCache
 set hashes=$get(%YHMFHashCalls),attempts=$get(%YHMFAttempts),started=$zut
 set status=$$insertchild^YHMFBAPI(warehouse,.path,.input)
 do sample("insert",status,started,hashes,attempts,measured)
 set path(6)=lot
 kill input
 set input("quantity")="2"
 if workload="cold" kill %YHMFDigestCache
 set hashes=$get(%YHMFHashCalls),attempts=$get(%YHMFAttempts),started=$zut
 set status=$$patchchild^YHMFBAPI(warehouse,.path,.input)
 do sample("patch",status,started,hashes,attempts,measured)
 if workload="cold" kill %YHMFDigestCache
 set hashes=$get(%YHMFHashCalls),attempts=$get(%YHMFAttempts),started=$zut
 set status=$$deletechild^YHMFBAPI(warehouse,.path)
 do sample("delete",status,started,hashes,attempts,measured)
 quit
sample(operation,status,started,hashes,attempts,measured)
 new elapsed
 set elapsed=$zut-started
 if status'="ok" write "error|",operation,"|",status,! set $ecode=",U2002,"
 if measured set samples(operation,index)=elapsed_"|"_($get(%YHMFHashCalls)-hashes)_"|"_($get(%YHMFAttempts)-attempts)
 quit

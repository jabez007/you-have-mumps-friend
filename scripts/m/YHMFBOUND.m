YHMFBOUND ; SPDX-License-Identifier: GPL-3.0-or-later
 quit
run(iterations,owned)
 new key,path,input,removed,i,started,status
 set key=^YHMFTestStorage("boundary","key")
 merge path=^YHMFTestStorage("boundary","path"),input=^YHMFTestStorage("boundary","input"),removed=path
 if owned set removed($order(path(""),-1)+1)="LOT"
 for i=1:1:iterations+3 do
 . set started=$zut
 . if owned set status=$$insertchild^OwnerAPI(key,.path,.input)
 . else  set status=$$insert^OwnerAPI(.input)
 . if status'="ok" write "insert|",status,! halt
 . if owned set status=$$deletechild^OwnerAPI(key,.removed)
 . else  set status=$$delete^OwnerAPI("LOT")
 . if status'="ok" write "delete|",status,! halt
 . if i>3 write $zut-started,!
 quit

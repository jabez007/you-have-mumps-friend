YHMFWTEST ; SPDX-License-Identifier: GPL-3.0-or-later
 quit
insert
 new input
 merge input=^YHMFTestStorage("writerInput")
 write $$insert^YHMFTAPI(.input),!
 quit
replace
 new input
 merge input=^YHMFTestStorage("writerInput")
 write $$replace^YHMFTAPI(.input),!
 quit
delete
 write $$delete^YHMFTAPI(^YHMFTestStorage("writerInput","id")),!
 quit
worker(id,sku)
 new input
 set input("id")=id,input("sku")=sku,input("name")="Stock",input("quantity")=1
 write $$insert^YHMFTAPI(.input),!
 quit
nested
 new input,status
 merge input=^YHMFTestStorage("writerInput")
 tstart ():serial
 set status=$$insert^YHMFTAPI(.input)
 write status,"|",$tlevel,!
 trollback
 quit
caught
 new input,$etrap
 merge input=^YHMFTestStorage("writerInput")
 set $etrap="write ""error|"",$tlevel,! set $ecode="""" halt"
 write $$insert^YHMFTAPI(.input),!
 quit
checkpoint
 new job
 write $$checkpoint^YHMFTAPI(^YHMFTestStorage("writerInput","raw"),.job),!
 quit
decimal
 new number,valid
 set valid=$$decimal^YHMFTAPI(^YHMFTestStorage("writerInput","value"),.number)
 write valid
 if valid write "|",number("units"),"|",$$dtoken^YHMFTAPI(^YHMFTestStorage("writerInput","value"))
 write !
 quit
counter
 write $$counter^YHMFTAPI(^YHMFTestStorage("writerInput","value"),^YHMFTestStorage("writerInput","delta")),!
 quit
refworker(id,product)
 new input
 set input("id")=id,input("product")=product
 write $$insert^YHMFTAPI(.input),!
 quit
refpair
 new product,lot,status,fail
 set lot("id")=^YHMFTestStorage("writerInput","id"),lot("product")=^YHMFTestStorage("writerInput","product")
 set product("id")=lot("product"),product("sku")="NEW-SKU",product("name")="New product"
 set fail=$get(^YHMFTestStorage("writerInput","fail"))
 tstart ():serial
 set product("id")=lot("product")
 set status=$$inserttx^YHMFPAPI(.product)
 if status'="ok" write status,"|",$tlevel,! quit
 set status=$$inserttx^YHMFTAPI(.lot)
 if status'="ok" write status,"|",$tlevel,! quit
 if fail do
 . set product("id")="COLLIDE"
 . set status=$$inserttx^YHMFPAPI(.product)
 if status'="ok" write status,"|",$tlevel,! quit
 tcommit
 write status,"|",$tlevel,!
 quit
hashes
 new key,value,result
 set key=""
 for  set key=$order(^YHMFTestStorage("writerInput",key)) quit:key=""  do
 . set value=^YHMFTestStorage("writerInput",key),result=$$sha256^YHMFTAPI(value)
 . write $select(result="":"invalid",1:result),!
 quit
digests
 new key,value,result,count
 kill %YHMFDigestCache,%YHMFHashCalls
 set key=""
 for  set key=$order(^YHMFTestStorage("writerInput",key)) quit:key=""  do
 . set value=^YHMFTestStorage("writerInput",key),result=$$locationhash^YHMFTAPI(value)
 . write $select(result="":"invalid",1:result),!
 set key="",count=0
 for  set key=$order(%YHMFDigestCache("values",key)) quit:key=""  set count=count+1
 write "calls|",$get(%YHMFHashCalls),"|entries|",count,"|declared|",$get(%YHMFDigestCache("count")),!
 quit
digestrestart
 new result
 kill %YHMFDigestCache,%YHMFHashCalls
 tstart ()
 set result=$$locationhash^YHMFTAPI("restart-safe-location")
 if $trestart=0 trestart
 trollback
 write result,"|calls|",$get(%YHMFHashCalls),!
 quit
digestversion
 kill %YHMFDigestCache,%YHMFHashCalls
 set %YHMFDigestCache="old-algorithm",%YHMFDigestCache("values","abc")="wrong",%YHMFDigestCache("count")=1000
 write $$locationhash^YHMFTAPI("abc"),"|",%YHMFDigestCache("count"),!
 quit
ownedcached
 new input,path,status
 do ownedpath(.path,.input,0)
 kill %YHMFDigestCache,%YHMFHashCalls
 set status=$$patchchild^YHMFTAPI("MAIN",.path,.input)
 if status'="ok" write status,! quit
 set input("quantity")=input("quantity")+1
 set status=$$patchchild^YHMFTAPI("MAIN",.path,.input)
 write status,"|calls|",$get(%YHMFHashCalls),!
 quit
arithmetic
 new key,value,result
 set key=""
 for  set key=$order(^YHMFTestStorage("writerInput",key)) quit:key=""  do
 . set value=^YHMFTestStorage("writerInput",key),result=$$dadd^YHMFTAPI($piece(value,"|"),$piece(value,"|",2))
 . write result
 . if result'="invalid" write "|",$$fromunits^YHMFTAPI(result)
 . write !
 quit
sources
 new key
 set key=""
 for  set key=$order(^YHMFTestStorage("writerInput",key)) quit:key=""  write $$sourceunits^YHMFTAPI(^YHMFTestStorage("writerInput",key)),!
 quit
ownedpath(path,input,insert)
 new count,key
 merge input=^YHMFTestStorage("writerInput")
 set path(1)="locations",path(2)=$get(input("_location"),"DOCK"),path(3)="bins",path(4)=$get(input("_bin"),"B1"),path(5)="balances"
 if 'insert set path(6)=$get(input("_key"),"LOT-001")
 set key=""
 for  set key=$order(input(key)) quit:key=""  if $extract(key)="_" kill input(key)
 quit
ownedinsert
 new input,path
 do ownedpath(.path,.input,1)
 write $$insertchild^YHMFTAPI("MAIN",.path,.input),!
 quit
ownedpatch
 new input,path
 do ownedpath(.path,.input,0)
 write $$patchchild^YHMFTAPI("MAIN",.path,.input),!
 quit
owneddelete
 new input,path
 do ownedpath(.path,.input,0)
 write $$deletechild^YHMFTAPI("MAIN",.path),!
 quit
ownedcaught
 new input,path,$etrap
 do ownedpath(.path,.input,0)
 set $etrap="write ""error|"",$tlevel,! set $ecode="""" halt"
 write $$patchchild^YHMFTAPI("MAIN",.path,.input),!
 quit
ownedpair
 new input,path,second,status,fail,firstlocation
 do ownedpath(.path,.input,0)
 set fail=$get(^YHMFTestStorage("writerInput","_fail")),second("quantity")=$select(fail:"-1",1:$get(^YHMFTestStorage("writerInput","_second"),"4.25"))
 set firstlocation=path(2)
 tstart ():serial
 set path(2)=firstlocation
 set status=$$patchchildtx^YHMFTAPI("MAIN",.path,.input)
 if status'="ok" write status,"|",$tlevel,! quit
 set path(2)="STORAGE",status=$$patchchildtx^YHMFTAPI("MAIN",.path,.second)
 if status'="ok" write status,"|",$tlevel,! quit
 tcommit
 write status,"|",$tlevel,!
 quit
ownedoutside
 new input,path
 do ownedpath(.path,.input,0)
 write $$patchchildtx^YHMFTAPI("MAIN",.path,.input),!
 quit
ownednested
 new input,path,status
 do ownedpath(.path,.input,0)
 tstart ():serial
 tstart ():serial
 set status=$$patchchild^YHMFTAPI("MAIN",.path,.input)
 write status,"|",$tlevel,!
 trollback
 quit
temporal
 new cfg
 do config^YHMFTAPI(.cfg)
 write $$valid^YHMFTAPI(^YHMFTestStorage("writerInput","day"),"day",.cfg),"|",$$valid^YHMFTAPI(^YHMFTestStorage("writerInput","instant"),"instant",.cfg),!
 quit
rootpatch
 new input,key
 merge input=^YHMFTestStorage("writerInput")
 set key=$get(input("_key"),"A") kill input("_key")
 write $$patch^YHMFTAPI(key,.input),!
 quit
rootpair
 new first,second,status
 set first("quantity")="2"
 merge second=^YHMFTestStorage("writerInput")
 tstart ():serial
 set status=$$patchtx^YHMFTAPI("A",.first)
 if status'="ok" write status,"|",$tlevel,! quit
 set status=$$patchtx^YHMFTAPI("B",.second)
 if status'="ok" write status,"|",$tlevel,! quit
 tcommit
 write status,"|",$tlevel,!
 quit
ownerpatch
 new input,path
 merge input=^YHMFTestStorage("writerInput")
 set path(1)="locations",path(2)="DOCK"
 write $$patchchild^YHMFTAPI("MAIN",.path,.input),!
 quit
jsonquote
 write $$quote^YHMFTAPI(^YHMFTestStorage("writerInput","value")),!
 quit
emptypath(path,input)
 new scope
 set scope=$get(input("_scope")) kill input("_scope")
 set path(1)="locations"
 if scope="bin" set path(2)="DOCK",path(3)="bins"
 quit
emptyinsert
 new input,path
 merge input=^YHMFTestStorage("writerInput")
 do emptypath(.path,.input)
 write $$insertchild^YHMFTAPI("MAIN",.path,.input),!
 quit
emptydelete
 new input,path,count
 merge input=^YHMFTestStorage("writerInput")
 do emptypath(.path,.input)
 set count=$order(path(""),-1),path(count+1)=input("id")
 write $$deletechild^YHMFTAPI("MAIN",.path),!
 quit
rootworker(key,name)
 new input
 set input("id")=key,input("name")=name
 write $$insert^YHMFTAPI(.input),!
 quit
rootcreatepair
 new first,second,status
 set first("id")="A",first("name")="first",second("id")="B",second("name")=^YHMFTestStorage("writerInput","name")
 tstart ():serial
 set status=$$inserttx^YHMFTAPI(.first)
 if status'="ok" write status,"|",$tlevel,! quit
 set status=$$inserttx^YHMFTAPI(.second)
 if status'="ok" write status,"|",$tlevel,! quit
 tcommit
 write status,"|",$tlevel,!
 quit
orderedkey
 write $$onumber^YHMFTAPI(^YHMFTestStorage("writerInput","value")),!
 quit
embeddedchild
 new input,path
 merge input=^YHMFTestStorage("writerInput")
 set path(1)="items"
 write $$insertchild^YHMFTAPI("MAIN",.path,.input),!
 quit
groupdelete
 new path
 set path(1)="groups",path(2)=^YHMFTestStorage("writerInput","id")
 write $$deletechild^YHMFTAPI("MAIN",.path),!
 quit
subdeleteworker
 new path
 set path(1)="locations",path(2)="DOCK",path(3)="bins",path(4)="B1"
 write $$deletechild^YHMFTAPI("MAIN",.path),!
 quit
subdeletecaught
 new $etrap
 set $etrap="write ""error|"",$tlevel,! set $ecode="""" halt"
 do subdeleteworker
 quit
inventoryreceipt
 new input
 merge input=^YHMFTestStorage("writerInput")
 write $$receive^YHMFRECEIPT(.input),!
 quit
inventorybinreceipt
 new input
 merge input=^YHMFTestStorage("writerInput")
 write $$receivebin^YHMFRECEIPT(.input),!
 quit

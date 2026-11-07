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

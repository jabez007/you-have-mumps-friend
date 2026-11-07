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

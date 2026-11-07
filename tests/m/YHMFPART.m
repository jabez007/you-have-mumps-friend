YHMFPART ; SPDX-License-Identifier: GPL-3.0-or-later
 quit
outside
 new input
 merge input=^YHMFTestStorage("writerInput")
 write $$inserttx^YHMFTAPI(.input),"|",$tlevel,!
 quit
pair
 new input,other,status
 merge input=^YHMFTestStorage("writerInput")
 set other("id")="B",other("value")=$get(input("second"),1)
 kill input("second")
 tstart ():serial
 set status=$$inserttx^YHMFTAPI(.input)
 if status'="ok" write status,"|",$tlevel,! quit
 set status=$$inserttx^YHMFOAPI(.other)
 if status'="ok" write status,"|",$tlevel,! quit
 write "pending|",$tlevel,!
 tcommit
 write status,"|",$tlevel,!
 quit
caught
 new $etrap
 set $etrap="write ""error|"",$tlevel,! set $ecode="""" halt"
 do pair
 quit
increment
 new input,other,status
 merge input=^YHMFTestStorage("writerInput")
 set other("id")="B",other("value")=1
 tstart ():serial
 ; Read current data again when the outer transaction restarts.
 set input("quantity")=$extract(^YHMFTestProduct("s:A","quantity"),3,99)+1
 set status=$$replacetx^YHMFTAPI(.input)
 if status'="ok" write status,"|",$tlevel,! quit
 set status=$$inserttx^YHMFOAPI(.other)
 if status'="ok" write status,"|",$tlevel,! quit
 tcommit
 write status,"|",$tlevel,!
 quit
replaceanddelete
 new input,status
 merge input=^YHMFTestStorage("writerInput")
 tstart ():serial
 set status=$$replacetx^YHMFTAPI(.input)
 if status'="ok" write status,"|",$tlevel,! quit
 set status=$$deletetx^YHMFOAPI("B")
 if status'="ok" write status,"|",$tlevel,! quit
 tcommit
 write status,"|",$tlevel,!
 quit
nestedpair
 tstart ():serial
 do pair
 if $tlevel tcommit
 write "outer|",$tlevel,!
 quit

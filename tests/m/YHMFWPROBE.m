YHMFWPROBE ; SPDX-License-Identifier: GPL-3.0-or-later
 quit
setTrigger ; Maintains the toy index and records creation-time visibility.
 if $ztoldval'="" kill ^YHMFTestStorage("triggerIndex",$ztoldval,id)
 set ^YHMFTestStorage("triggerIndex",$ztvalue,id)=1
 set ^YHMFTestStorage("sawIdentity")=$data(^YHMFTestStorage("triggerRecords",id,"id"))
 set ^YHMFTestStorage("setCalls")=$get(^YHMFTestStorage("setCalls"))+1
 quit
killTrigger
 kill ^YHMFTestStorage("triggerIndex",$ztoldval,id)
 set ^YHMFTestStorage("killCalls")=$get(^YHMFTestStorage("killCalls"))+1
 quit
triggerCreate
 tstart ():serial
 set ^YHMFTestStorage("triggerRecords",1,"value")="s:one"
 set ^YHMFTestStorage("triggerRecords",1,"id")="i:1"
 tcommit
 quit
triggerPatch
 tstart ():serial
 set ^YHMFTestStorage("triggerRecords",1,"value")="s:two"
 tcommit
 quit
triggerRollback
 tstart ():serial
 set ^YHMFTestStorage("triggerRecords",1,"value")="s:rolledBack"
 trollback
 quit
triggerLeafKill
 kill ^YHMFTestStorage("triggerRecords",1,"value")
 quit
triggerParentKill
 kill ^YHMFTestStorage("triggerRecords",1)
 quit
apiCreate
 write $$write("insert",1,"one"),!
 quit
apiPatch
 write $$write("replace",1,"two"),!
 quit
apiInvalid
 write $$write("replace",1,""),!
 quit
apiDelete
 write $$write("delete",1,""),!
 quit
write(operation,id,value) ; Fixed-schema prototype; not a public/generated API.
 new old,status
 if $tlevel quit "nested"
 if (id'?1.18N)!(id<1) quit "invalid"
 if (operation'="delete"),($length(value)<1) quit "invalid"
 tstart ():serial
 set old=$get(^YHMFTestStorage("apiRecords",id,"value")),status="ok"
 if (operation="insert"),$data(^YHMFTestStorage("apiRecords",id)) set status="exists"
 if (operation'="insert"),'$data(^YHMFTestStorage("apiRecords",id)) set status="missing"
 if status'="ok" trollback  quit status
 if old'="" kill ^YHMFTestStorage("apiIndex",old,id)
 if operation="delete" kill ^YHMFTestStorage("apiRecords",id)
 else  do
 . set ^YHMFTestStorage("apiRecords",id,"id")="i:"_id
 . set ^YHMFTestStorage("apiRecords",id,"value")="s:"_value
 . set ^YHMFTestStorage("apiIndex","s:"_value,id)=1
 tcommit
 quit status

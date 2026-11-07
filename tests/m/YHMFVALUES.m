YHMFVALUES ; SPDX-License-Identifier: GPL-3.0-or-later
 new i,k,raw,tag
 set i=0
 for  set i=$order(^YHMFTestStorage("golden",i)) quit:i=""  do
 . set raw=^YHMFTestStorage("golden",i),tag=^YHMFTestStorage("tags",i)
 . write "golden|",i,"|",$$isnull^YHMFREAD(raw),"|",$$payload^YHMFREAD(raw,tag),!
 write "tagged_add|",(^YHMFTestStorage("tagged","decimal")+1),!
 write "payload_add|",($$payload^YHMFREAD(^YHMFTestStorage("tagged","decimal"),"d")+1),!
 write "plain_add|",(^YHMFTestStorage("plain","decimal")+1),!
 write "precise_arithmetic|",(^YHMFTestStorage("plain","precise")+0),!
 write "precise_payload|",$$payload^YHMFREAD(^YHMFTestStorage("tagged","precise"),"d"),!
 write "large_arithmetic|",(^YHMFTestStorage("plain","large")+1),!
 write "large_payload|",$$payload^YHMFREAD(^YHMFTestStorage("tagged","large"),"i"),!
 write "plain_bool_int_equal|",(^YHMFTestStorage("plain","bool")=^YHMFTestStorage("plain","integer")),!
 write "tagged_bool_int_equal|",(^YHMFTestStorage("tagged","bool")=^YHMFTestStorage("tagged","integer")),!
 write "null_empty_equal|",(^YHMFTestStorage("tagged","null")=^YHMFTestStorage("tagged","empty")),!
 set k=""
 for  set k=$order(^YHMFTestStorage("numeric_keys",k)) quit:k=""  write "numeric_key|",k,!
 set k=""
 for  set k=$order(^YHMFTestStorage("string_keys",k)) quit:k=""  write "string_key|",k,!
 set k=""
 for  set k=$order(^YHMFTestStorage("index_tokens",k)) quit:k=""  write "index_token|",k,!
 set ^YHMFTestStorage("key_collision",2)="integer"
 set ^YHMFTestStorage("key_collision","2")="string"
 write "native_string_numeric_key_collision|",^YHMFTestStorage("key_collision",2),!
 write "engine|",$zversion,!
 quit

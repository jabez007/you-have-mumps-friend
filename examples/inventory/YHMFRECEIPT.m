YHMFRECEIPT ; SPDX-License-Identifier: GPL-3.0-or-later
 ; Export WarehouseAPI and MovementAPI from the same inventory registry first.
 quit
receive(input)
 new request,status,$etrap
 if $tlevel quit "nested"
 merge request=input
 set $etrap="if $tlevel trollback"
 tstart ():serial
 set status=$$attempt(.request)
 if status'="ok" do stableabort^MovementAPI quit status
 tcommit
 quit "ok"
attempt(request)
 new key,field,lot,product,warehouse,location,bin,root,raw,old,amount,total,path,balance,movement,status
 ; Keep this example's direct addresses within a small, documented input domain.
 if $data(request)#2 quit "invalid"
 set field=""
 for  set field=$order(request(field)) quit:field=""  do  quit:field="invalid"
 . if $data(request(field))'=1 set field="invalid" quit
 . if $find(",id,lot,product,warehouse,location,bin,quantity,reason,occurred_at,",","_field_",")=0 set field="invalid"
 if field="invalid" quit "invalid"
 for field="id","lot","product","warehouse","location","bin" do  quit:field="invalid"
 . set key=$get(request(field))
 . if key=""!($zlength(key)>64)!(key[$char(0))!'$$utf8^MovementAPI(key) set field="invalid"
 if field="invalid" quit "invalid"
 set lot=request("lot"),product=request("product"),warehouse=request("warehouse"),location=request("location"),bin=request("bin")
 if $get(^InventoryLot("s:"_lot,"product"))'=("r:s:"_product) quit "references"
 set amount=$$sourceunits^WarehouseAPI($get(request("quantity")))
 if amount="invalid"!(amount="0")!($extract(amount)="-") quit "invalid"
 set root=$name(^InventoryWarehouse("s:"_warehouse,"locations","s:"_location,"bins","s:"_bin,"balances","s:"_lot))
 set old="0"
 if $data(@root) do  quit:old="invalid" "corrupt"
 . if $get(@root@("lot"))'=("r:s:"_lot)!($get(@root@("product"))'=("r:s:"_product)) set old="invalid" quit
 . set raw=$get(@root@("quantity"))
 . if $zextract(raw,1,2)'="d:" set old="invalid" quit
 . set old=$$sourceunits^WarehouseAPI($zextract(raw,3,$zlength(raw)))
 set total=$$fromunits^WarehouseAPI($$dadd^WarehouseAPI(old,amount))
 if total="invalid" quit "invalid"
 set path(1)="locations",path(2)=location,path(3)="bins",path(4)=bin,path(5)="balances"
 set balance("quantity")=total
 if $data(@root) set path(6)=lot,status=$$patchchildtx^WarehouseAPI(warehouse,.path,.balance)
 else  do
 . set balance("id")=lot,balance("lot")=lot,balance("product")=product
 . set status=$$insertchildtx^WarehouseAPI(warehouse,.path,.balance)
 if status'="ok" quit status
 ; Receipt checkpoint: tests can coordinate a competing writer before ledger insertion.
 set movement("id")=request("id"),movement("kind")="receive",movement("lot")=lot,movement("product")=product
 set movement("quantity")=$get(request("quantity")),movement("reason")=$get(request("reason")),movement("occurred_at")=$get(request("occurred_at"))
 set movement("source","%null")=1,movement("destination","warehouse")=warehouse,movement("destination","location")=location,movement("destination","bin")=bin
 quit $$inserttx^MovementAPI(.movement)

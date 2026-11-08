YHMFRECEIPT ; SPDX-License-Identifier: GPL-3.0-or-later
 ; Export WarehouseAPI and MovementAPI from the same inventory registry first.
 quit
receive(input)
 quit $$write(.input,0)
receivebin(input)
 quit $$write(.input,1)
write(input,mode)
 new request,status,$etrap
 if $tlevel quit "nested"
 merge request=input
 set $etrap="if $tlevel trollback"
 tstart ():serial
 set status=$$attempt(.request,mode)
 if status'="ok" do stableabort^MovementAPI quit status
 tcommit
 quit "ok"
attempt(request,mode)
 new key,field,lot,product,warehouse,location,bin,root,raw,old,amount,total,path,balance,movement,status
 if mode set status=$$ready() if status'="ok" quit status
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
 set status=$$address(warehouse,location,bin,lot,mode,.key,.root,.path)
 if status'="ok" quit status
 set old="0"
 if $data(@root) do  quit:old="invalid" "corrupt"
 . if $get(@root@("lot"))'=("r:s:"_lot)!($get(@root@("product"))'=("r:s:"_product)) set old="invalid" quit
 . set raw=$get(@root@("quantity"))
 . if $zextract(raw,1,2)'="d:" set old="invalid" quit
 . set old=$$sourceunits^WarehouseAPI($zextract(raw,3,$zlength(raw)))
 set total=$$fromunits^WarehouseAPI($$dadd^WarehouseAPI(old,amount))
 if total="invalid" quit "invalid"
 set balance("quantity")=total
 if $data(@root) do
 . set path($order(path(""),-1)+1)=lot
 . if mode set status=$$patchchildtx^BinAPI(key,.path,.balance)
 . else  set status=$$patchchildtx^WarehouseAPI(key,.path,.balance)
 else  do
 . set balance("id")=lot,balance("lot")=lot,balance("product")=product
 . if mode set status=$$insertchildtx^BinAPI(key,.path,.balance)
 . else  set status=$$insertchildtx^WarehouseAPI(key,.path,.balance)
 if status'="ok" quit status
 ; Receipt checkpoint: tests can coordinate a competing writer before ledger insertion.
 set movement("id")=request("id"),movement("kind")="receive",movement("lot")=lot,movement("product")=product
 set movement("quantity")=$get(request("quantity")),movement("reason")=$get(request("reason")),movement("occurred_at")=$get(request("occurred_at"))
 set movement("source","%null")=1,movement("destination","warehouse")=warehouse,movement("destination","location")=location,movement("destination","bin")=bin
 quit $$inserttx^MovementAPI(.movement)
ready()
 new cfg,values,status,root
 if '$data(^InventoryStockMove) quit "ok"
 if '$$available^StockMoveAPI("InventoryStockMove") quit "maintenance"
 do config^StockMoveAPI(.cfg)
 if '$$schema^StockMoveAPI(cfg("global"),cfg("fingerprint"),cfg("snapshot")) quit "schema"
 set root=$name(^InventoryStockMove("s:bins-v1")),status=$$old^StockMoveAPI(root,.cfg,.values)
 if status'="ok" quit status
 if values("id")'="bins-v1" quit "corrupt"
 quit $select(values("status")="complete":"ok",1:"maintenance")
address(warehouse,location,bin,lot,mode,key,root,path)
 new parent
 if 'mode do  quit "ok"
 . set key=warehouse,path(1)="locations",path(2)=location,path(3)="bins",path(4)=bin,path(5)="balances"
 . set root=$name(^InventoryWarehouse("s:"_warehouse,"locations","s:"_location,"bins","s:"_bin,"balances","s:"_lot))
 set parent=$zlength(warehouse)_":"_warehouse_$zlength(location)_":"_location,key=parent_$zlength(bin)_":"_bin
 if $get(^InventoryLocation("s:"_parent,"warehouse"))'=("r:s:"_warehouse)!($get(^InventoryLocation("s:"_parent,"code"))'=("s:"_location)) quit "references"
 if $get(^InventoryBin("s:"_key,"warehouse"))'=("r:s:"_warehouse)!($get(^InventoryBin("s:"_key,"location"))'=("r:s:"_parent)) quit "references"
 if $get(^InventoryBin("s:"_key,"code"))'=("s:"_bin) quit "references"
 set path(1)="balances",root=$name(^InventoryBin("s:"_key,"balances","s:"_lot))
 quit "ok"

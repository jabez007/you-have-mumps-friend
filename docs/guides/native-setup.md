# Set up a YottaDB database

`MemoryStore` needs no setup and lasts only as long as the Python process. Use these steps
to store data durably with `NativeStore`.

## Install YottaDB and the driver

1. [Install YottaDB](https://docs.yottadb.com/AdminOpsGuide/installydb.html). CI qualifies
   r2.02 on Ubuntu 22.04 and r2.06 on Ubuntu 24.04, both with Python 3.12. An unpacked
   binary archive is not enough, because YottaDB needs a correctly installed `gtmsecshr`
   helper.
2. Set `ydb_dist` to the installation directory.
3. Install YHMF with the `native` extra, which adds YDBPython:

   ```sh
   pip install "mccann-hub-yhmf[native] @ git+https://github.com/jabez007/you-have-mumps-friend"
   ```

## Create a database

The default region is too small for most models. It holds 256-byte records and 64-byte
keys, and YHMF stores each model's schema snapshot in one record. In a live test on r2.06,
a one-field model fit, and the first write of a two-field model failed with
`%YDB-E-REC2BIG`.

Create the region with larger limits. These are the values that
[`scripts/test-native.sh`](../../scripts/test-native.sh) uses:

```sh
export ydb_gbldir="$PWD/app.gld"
export ydb_routines="$ydb_dist/libyottadbutil.so $ydb_dist"
export LD_LIBRARY_PATH="$ydb_dist"

"$ydb_dist/yottadb" -run GDE <<EOF
change -segment DEFAULT -file=$PWD/app.dat
change -region DEFAULT -key_size=1019 -record_size=32767
exit
EOF
"$ydb_dist/mupip" create
```

Keep `ydb_gbldir`, `ydb_routines`, and `LD_LIBRARY_PATH` set in every process that opens
this database.

## Connect from Python

Replace `MemoryStore()` with `NativeStore()`:

```text
from mccann_hub.yhmf import Database, NativeStore, Registry

db = Database(NativeStore(), Registry(Product))
```

The first write to a model installs its schema, as long as its global is empty. To bring in
existing data, see [adopt an existing global](adoption.md).

Use processes, not threads, for parallel work. YDBPython uses process-based concurrency.

## Protect your data

YHMF does not configure journaling, backup, or replication. Those stay YottaDB tasks. See the
[YottaDB administration guide](https://docs.yottadb.com/AdminOpsGuide/).

Never run the test suite against this database. See [CONTRIBUTING](../../CONTRIBUTING.md).

Next, [read and write the data from M](m-access.md).

# Component licenses

Checked against repository declarations and upstream notices on 2026-11-06. This
page records sources; it is not a legal review of an application or deployment.

| Component | License information | Terms |
| --- | --- | --- |
| Original YHMF source | GPL-3.0-or-later, declared by this project | [LICENSE](../LICENSE), [package declaration](../pyproject.toml) |
| YottaDB | AGPLv3; upstream's public notice specifies AGPLv3-or-later when software arrives without clear licensing terms | [YottaDB license notice](https://yottadb.com/product/license/), [engine COPYING](https://github.com/YottaDB/YDB/blob/master/COPYING) |
| YDBPython, the optional native driver | AGPLv3 in COPYING, with a separate upstream LICENSE notice | [driver COPYING](https://github.com/YottaDB/YDBPython/blob/master/COPYING), [driver LICENSE](https://github.com/YottaDB/YDBPython/blob/master/LICENSE) |
| Pydantic, the Python runtime dependency | MIT; also declared by installed Pydantic 2.14.3 metadata | [Pydantic LICENSE](https://github.com/pydantic/pydantic/blob/main/LICENSE) |

The driver's LICENSE file contains upstream clarifications in addition to the license
text in COPYING. Consult both, and the notices bundled with the exact versions you use.
YHMF's license does not replace those upstream terms. The optional dependency is declared
under `native` in [pyproject.toml](../pyproject.toml); MemoryStore does not import the driver.

This project has not obtained counsel review of distribution, linking, proprietary
applications, or network-service deployment. This page makes no conclusion about those
uses. [Issue 35](https://github.com/jabez007/you-have-mumps-friend/issues/35) remains open
for that review. Any deployment-specific guidance requires it before publication.

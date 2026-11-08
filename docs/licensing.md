# Component licenses

Checked against repository declarations, installed package metadata, and upstream
notices on 2026-11-08. This page records sources. The project has had no legal review,
and this page gives no legal advice.

| Component | License information | Terms |
| --- | --- | --- |
| Original YHMF source | GPL-3.0-or-later, declared by this project | [LICENSE](../LICENSE), [package declaration](../pyproject.toml) |
| YottaDB | AGPLv3; upstream's public notice specifies AGPLv3-or-later when software arrives without clear licensing terms | [YottaDB license notice](https://yottadb.com/product/license/), [engine COPYING](https://github.com/YottaDB/YDB/blob/master/COPYING) |
| YDBPython, the optional native driver | AGPLv3 in COPYING, with a separate upstream LICENSE notice | [driver COPYING](https://github.com/YottaDB/YDBPython/blob/master/COPYING), [driver LICENSE](https://github.com/YottaDB/YDBPython/blob/master/LICENSE) |
| Pydantic, the Python runtime dependency | MIT; also declared by installed Pydantic 2.14.3 metadata | [Pydantic LICENSE](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| pydantic-core 2.50.4, annotated-types 0.8.0, and typing-inspection 0.4.5, installed with Pydantic | MIT, declared by installed metadata | The `LICENSE` file in each installed package |
| typing-extensions 4.16.0, installed with Pydantic | PSF-2.0, declared by installed metadata | The `LICENSE` file in the installed package |

The driver's LICENSE file contains upstream clarifications in addition to the license
text in COPYING. Consult both, and the notices bundled with the exact versions you use.
YHMF's license does not replace those upstream terms.

## Generated and sample M code

`Database.m_routine` copies YHMF's M templates into each routine it generates and adds a
`GPL-3.0-or-later` SPDX line. The sample [YHMFREAD.m](../examples/m/YHMFREAD.m) has the
same line.

## What YHMF includes

The `mccann-hub-yhmf` wheel and this repository contain no YottaDB or YDBPython code.
You install YottaDB separately. The `native` extra in
[pyproject.toml](../pyproject.toml) installs the driver, and MemoryStore does not import
it.

## What this page does not cover

The project publishes these license facts and no guidance on distribution, linking,
proprietary applications, or network-service deployment.
[Issue 35](https://github.com/jabez007/you-have-mumps-friend/issues/35) records this
decision.


## Candidate distribution review

Candidate artifact checks compare the bundled GPL LICENSE byte-for-byte with this
repository. Generated M templates retain their SPDX declarations. Source distributions
exclude developer state; neither artifact bundles the YottaDB engine or YDBPython.
The consumer fixture uses GPL-3.0-or-later original code as well.

The installed runtime notices checked on 2026-11-08 agree with the table above,
including YDBPython 2.0.1's AGPLv3 metadata and both its COPYING and LICENSE files.
The public YottaDB license notice was checked again on that date. Runtime extra
installation changes which components an evaluator must review; it does not replace
the engine's installation or license obligations. Development/build tools are not
runtime dependencies and are not bundled into the wheel.

Before distributing an application or exposing a service, the maintainer must review
the exact driver/engine notices, generated-code obligations, application licensing
and deployment facts. This project does not establish proprietary compatibility or
answer application-specific linking or network-service questions. Those remain
maintainer/legal review decisions, separate from technical candidate qualification.

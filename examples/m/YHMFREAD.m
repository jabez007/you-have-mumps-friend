YHMFREAD ; SPDX-License-Identifier: GPL-3.0-or-later
 ; Explicit-prefix helpers for layout-one readers, not codecs or validators.
 quit
payload(raw,tag)
 if $length(tag)'=1 set $ecode=",U1001,"
 if $extract(raw,1,2)'=(tag_":") set $ecode=",U1001,"
 quit $extract(raw,3,$length(raw))
isnull(raw)
 quit raw="n:"

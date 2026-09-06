# Languages

`--language` takes any row of this table either way: the tag
(`--language zh-hant`) or the name (`--language "traditional chinese"`,
case-insensitive). The tag is the mechanical half — stamped on the
inserted markup and `dc:language`, recorded in the provenance record,
and it names the structured-output reply field (`zh_hant_translation`).
The name is the prose half: what the model is actually asked for.

For a language this table misses, pass both halves yourself as
`TAG:NAME`, split on the first colon — for example
`--language "gsw:Swiss German"`. The tag then drives the stamps and
field names exactly as if it were listed here; the name goes to the
model. A free-typed name matching nothing still runs — the run prints
one `Note:` line saying nothing will be stamped on the output.

The source language is never part of `--language`; `--source_lang`
states it when auto-detection isn't enough.

## Tags

| Tag | Name |
| --- | --- |
| `aa` | afar |
| `ab` | abkhaz |
| `ace` | acehnese |
| `af` | afrikaans |
| `ak` | akan |
| `am` | amharic |
| `an` | aragonese |
| `ar` | arabic |
| `arc` | aramaic |
| `as` | assamese |
| `ast` | asturian |
| `ay` | aymara |
| `az` | azerbaijani |
| `ba` | bashkir |
| `ban` | balinese |
| `bcl` | bikol |
| `be` | belarusian |
| `bg` | bulgarian |
| `bho` | bhojpuri |
| `bi` | bislama |
| `bm` | bambara |
| `bn` | bengali |
| `bo` | tibetan |
| `br` | breton |
| `brx` | bodo |
| `bs` | bosnian |
| `bug` | buginese |
| `ca` | catalan |
| `ce` | chechen |
| `ceb` | cebuano |
| `ch` | chamorro |
| `chr` | cherokee |
| `ckb` | central kurdish |
| `co` | corsican |
| `cr` | cree |
| `cs` | czech |
| `cu` | church slavonic |
| `cv` | chuvash |
| `cy` | welsh |
| `da` | danish |
| `de` | german |
| `doi` | dogri |
| `dv` | dhivehi |
| `dz` | dzongkha |
| `ee` | ewe |
| `el` | greek |
| `en` | english |
| `en-gb` | british english |
| `en-us` | american english |
| `eo` | esperanto |
| `es` | spanish |
| `es-419` | latin american spanish |
| `es-mx` | mexican spanish |
| `et` | estonian |
| `eu` | basque |
| `fa` | persian |
| `ff` | fulah |
| `fi` | finnish |
| `fil` | filipino |
| `fj` | fijian |
| `fo` | faroese |
| `fr` | french |
| `fr-ca` | canadian french |
| `fur` | friulian |
| `fy` | western frisian |
| `ga` | irish |
| `gd` | scottish gaelic |
| `gl` | galician |
| `gn` | guarani |
| `grc` | ancient greek |
| `gsw` | swiss german |
| `gu` | gujarati |
| `gv` | manx |
| `ha` | hausa |
| `haw` | hawaiian |
| `he` | hebrew |
| `hi` | hindi |
| `hil` | hiligaynon |
| `hr` | croatian |
| `ht` | haitian creole |
| `hu` | hungarian |
| `hy` | armenian |
| `ia` | interlingua |
| `id` | indonesian |
| `ig` | igbo |
| `ilo` | ilocano |
| `is` | icelandic |
| `it` | italian |
| `iu` | inuktitut |
| `ja` | japanese |
| `jw` | javanese |
| `ka` | georgian |
| `kam` | kamba |
| `kg` | kongo |
| `ki` | kikuyu |
| `kk` | kazakh |
| `kl` | greenlandic |
| `km` | khmer |
| `kn` | kannada |
| `ko` | korean |
| `kok` | konkani |
| `ks` | kashmiri |
| `ku` | kurdish |
| `kv` | komi |
| `kw` | cornish |
| `ky` | kyrgyz |
| `la` | latin |
| `lb` | luxembourgish |
| `lg` | ganda |
| `ln` | lingala |
| `lo` | lao |
| `lt` | lithuanian |
| `lu` | luba-katanga |
| `luo` | luo |
| `lv` | latvian |
| `mad` | madurese |
| `mai` | maithili |
| `mer` | meru |
| `mg` | malagasy |
| `mi` | maori |
| `min` | minangkabau |
| `mk` | macedonian |
| `ml` | malayalam |
| `mn` | mongolian |
| `mni` | manipuri |
| `mr` | marathi |
| `ms` | malay |
| `mt` | maltese |
| `my` | myanmar |
| `myv` | erzya |
| `nb` | norwegian bokmal |
| `nds` | low german |
| `ne` | nepali |
| `new` | newari |
| `nl` | dutch |
| `nn` | nynorsk |
| `no` | norwegian |
| `nr` | southern ndebele |
| `nso` | northern sotho |
| `nv` | navajo |
| `ny` | chichewa |
| `nyn` | nyankole |
| `oc` | occitan |
| `oj` | ojibwe |
| `om` | oromo |
| `or` | odia |
| `os` | ossetian |
| `pa` | punjabi |
| `pam` | kapampangan |
| `pap` | papiamento |
| `pi` | pali |
| `pl` | polish |
| `ps` | pashto |
| `pt` | portuguese |
| `pt-br` | brazilian portuguese |
| `pt-pt` | european portuguese |
| `qu` | quechua |
| `rm` | romansh |
| `rn` | kirundi |
| `ro` | romanian |
| `ru` | russian |
| `rw` | kinyarwanda |
| `sa` | sanskrit |
| `sah` | yakut |
| `sat` | santali |
| `sc` | sardinian |
| `scn` | sicilian |
| `sco` | scots |
| `sd` | sindhi |
| `se` | northern sami |
| `sg` | sango |
| `shn` | shan |
| `si` | sinhala |
| `sk` | slovak |
| `sl` | slovenian |
| `sm` | samoan |
| `sn` | shona |
| `so` | somali |
| `sq` | albanian |
| `sr` | serbian |
| `ss` | swati |
| `st` | southern sotho |
| `su` | sundanese |
| `sv` | swedish |
| `sw` | swahili |
| `syr` | syriac |
| `ta` | tamil |
| `te` | telugu |
| `tg` | tajik |
| `th` | thai |
| `ti` | tigrinya |
| `tk` | turkmen |
| `tl` | tagalog |
| `tn` | tswana |
| `to` | tongan |
| `tpi` | tok pisin |
| `tr` | turkish |
| `ts` | tsonga |
| `tt` | tatar |
| `ty` | tahitian |
| `udm` | udmurt |
| `ug` | uyghur |
| `uk` | ukrainian |
| `umb` | umbundu |
| `ur` | urdu |
| `uz` | uzbek |
| `ve` | venda |
| `vi` | vietnamese |
| `wa` | walloon |
| `war` | waray |
| `wo` | wolof |
| `xh` | xhosa |
| `yi` | yiddish |
| `yo` | yoruba |
| `zh` | simplified chinese |
| `zh-cn` | mainland chinese |
| `zh-hans` | simplified chinese |
| `zh-hant` | traditional chinese |
| `zh-hk` | hong kong chinese |
| `zh-tw` | taiwan mandarin |
| `zh-yue` | cantonese |
| `zu` | zulu |

## Other spellings

Names that are not the one printed above, accepted for the same tag.

| You may type | Tag |
| --- | --- |
| `bokmal` | `nb` |
| `brazilian` | `pt-br` |
| `burmese` | `my` |
| `castilian` | `es` |
| `chewa` | `ny` |
| `divehi` | `dv` |
| `farsi` | `fa` |
| `flemish` | `nl` |
| `frisian` | `fy` |
| `haitian` | `ht` |
| `irish gaelic` | `ga` |
| `isixhosa` | `xh` |
| `isizulu` | `zu` |
| `kalaallisut` | `kl` |
| `kirghiz` | `ky` |
| `letzeburgesch` | `lb` |
| `maldivian` | `dv` |
| `mandarin` | `zh-hans` |
| `mandarin chinese` | `zh-hans` |
| `moldavian` | `ro` |
| `moldovan` | `ro` |
| `nyanja` | `ny` |
| `oriya` | `or` |
| `panjabi` | `pa` |
| `pushto` | `ps` |
| `scots gaelic` | `gd` |
| `sesotho` | `st` |
| `setswana` | `tn` |
| `sinhalese` | `si` |
| `sorani` | `ckb` |
| `twi` | `ak` |
| `uighur` | `ug` |
| `valencian` | `ca` |

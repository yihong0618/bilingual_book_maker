# Languages

`--language` takes any row of this table either way: the tag
(`--language zh-hant`) or the name (`--language "traditional chinese"`,
case-insensitive). The tag is the mechanical half — stamped on the
inserted markup and `dc:language`, recorded in the provenance record,
and it names the structured-output reply field (`zh_hant_translation`).
The name is the prose half: what the model is actually asked for.

For a language this table misses, pass both halves yourself as
`TAG:NAME`, split on the first colon — for example
`--language "ain:Ainu"`. The tag then drives the stamps and
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
| `akk` | akkadian |
| `am` | amharic |
| `an` | aragonese |
| `ang` | old english |
| `apc` | levantine arabic |
| `ar` | arabic |
| `arc` | aramaic |
| `arn` | mapudungun |
| `ary` | moroccan arabic |
| `arz` | egyptian arabic |
| `as` | assamese |
| `ast` | asturian |
| `av` | avar |
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
| `cop` | coptic |
| `cr` | cree |
| `cs` | czech |
| `csb` | kashubian |
| `cu` | church slavonic |
| `cv` | chuvash |
| `cy` | welsh |
| `da` | danish |
| `de` | german |
| `de-at` | austrian german |
| `de-ch` | swiss standard german |
| `doi` | dogri |
| `dsb` | lower sorbian |
| `dv` | dhivehi |
| `dz` | dzongkha |
| `ee` | ewe |
| `egy` | ancient egyptian |
| `el` | greek |
| `en` | english |
| `en-au` | australian english |
| `en-ca` | canadian english |
| `en-gb` | british english |
| `en-in` | indian english |
| `en-us` | american english |
| `enm` | middle english |
| `eo` | esperanto |
| `es` | spanish |
| `es-419` | latin american spanish |
| `es-ar` | argentine spanish |
| `es-es` | european spanish |
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
| `fro` | old french |
| `fur` | friulian |
| `fy` | western frisian |
| `ga` | irish |
| `gd` | scottish gaelic |
| `gl` | galician |
| `gn` | guarani |
| `goh` | old high german |
| `got` | gothic |
| `grc` | ancient greek |
| `gsw` | swiss german |
| `gu` | gujarati |
| `gv` | manx |
| `ha` | hausa |
| `hak` | hakka |
| `haw` | hawaiian |
| `he` | hebrew |
| `hi` | hindi |
| `hil` | hiligaynon |
| `hit` | hittite |
| `hr` | croatian |
| `hsb` | upper sorbian |
| `ht` | haitian creole |
| `hu` | hungarian |
| `hy` | armenian |
| `ia` | interlingua |
| `id` | indonesian |
| `ie` | interlingue |
| `ig` | igbo |
| `ilo` | ilocano |
| `io` | ido |
| `is` | icelandic |
| `it` | italian |
| `iu` | inuktitut |
| `ja` | japanese |
| `jam` | jamaican patois |
| `jbo` | lojban |
| `jw` | javanese |
| `ka` | georgian |
| `kab` | kabyle |
| `kam` | kamba |
| `kbd` | kabardian |
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
| `lad` | ladino |
| `lb` | luxembourgish |
| `lez` | lezgian |
| `lg` | ganda |
| `lij` | ligurian |
| `lld` | ladin |
| `lmo` | lombard |
| `ln` | lingala |
| `lo` | lao |
| `lt` | lithuanian |
| `lu` | luba-katanga |
| `luo` | luo |
| `lv` | latvian |
| `lzh` | literary chinese |
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
| `mwl` | mirandese |
| `my` | myanmar |
| `myv` | erzya |
| `nah` | nahuatl |
| `nan` | hokkien |
| `nap` | neapolitan |
| `nb` | norwegian bokmal |
| `nds` | low german |
| `ne` | nepali |
| `new` | newari |
| `nl` | dutch |
| `nl-be` | belgian dutch |
| `nn` | nynorsk |
| `no` | norwegian |
| `non` | old norse |
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
| `pal` | middle persian |
| `pam` | kapampangan |
| `pap` | papiamento |
| `pi` | pali |
| `pl` | polish |
| `pms` | piedmontese |
| `ps` | pashto |
| `pt` | portuguese |
| `pt-br` | brazilian portuguese |
| `pt-pt` | european portuguese |
| `qu` | quechua |
| `rm` | romansh |
| `rn` | kirundi |
| `ro` | romanian |
| `ru` | russian |
| `rue` | rusyn |
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
| `sga` | old irish |
| `shn` | shan |
| `si` | sinhala |
| `sk` | slovak |
| `sl` | slovenian |
| `sm` | samoan |
| `sn` | shona |
| `so` | somali |
| `sq` | albanian |
| `sr` | serbian |
| `sr-cyrl` | serbian cyrillic |
| `sr-latn` | serbian latin |
| `srn` | sranan tongo |
| `ss` | swati |
| `st` | southern sotho |
| `su` | sundanese |
| `sux` | sumerian |
| `sv` | swedish |
| `sw` | swahili |
| `syr` | syriac |
| `szl` | silesian |
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
| `tyv` | tuvan |
| `tzm` | central atlas tamazight |
| `udm` | udmurt |
| `ug` | uyghur |
| `uk` | ukrainian |
| `umb` | umbundu |
| `ur` | urdu |
| `uz` | uzbek |
| `ve` | venda |
| `vec` | venetian |
| `vi` | vietnamese |
| `vo` | volapuk |
| `wa` | walloon |
| `war` | waray |
| `wo` | wolof |
| `wuu` | wu chinese |
| `xcl` | classical armenian |
| `xh` | xhosa |
| `yi` | yiddish |
| `yo` | yoruba |
| `yua` | yucatec maya |
| `zh` | chinese |
| `zh-cn` | mainland chinese |
| `zh-hans` | simplified chinese |
| `zh-hant` | traditional chinese |
| `zh-hk` | hong kong chinese |
| `zh-sg` | singapore chinese |
| `zh-tw` | taiwan mandarin |
| `zh-yue` | cantonese |
| `zu` | zulu |

## Other spellings

Names that are not the one printed above, accepted for the same tag.

| You may type | Tag |
| --- | --- |
| `bahasa indonesia` | `id` |
| `bahasa melayu` | `ms` |
| `bokmal` | `nb` |
| `brazilian` | `pt-br` |
| `burmese` | `my` |
| `castilian` | `es` |
| `chewa` | `ny` |
| `classical chinese` | `lzh` |
| `divehi` | `dv` |
| `farsi` | `fa` |
| `flemish` | `nl` |
| `frisian` | `fy` |
| `haitian` | `ht` |
| `irish gaelic` | `ga` |
| `isixhosa` | `xh` |
| `isizulu` | `zu` |
| `jamaican creole` | `jam` |
| `judeo-spanish` | `lad` |
| `kalaallisut` | `kl` |
| `kirghiz` | `ky` |
| `letzeburgesch` | `lb` |
| `maldivian` | `dv` |
| `mandarin` | `zh` |
| `mandarin chinese` | `zh` |
| `min nan` | `nan` |
| `modern standard arabic` | `ar` |
| `moldavian` | `ro` |
| `moldovan` | `ro` |
| `norwegian nynorsk` | `nn` |
| `nyanja` | `ny` |
| `old church slavonic` | `cu` |
| `oriya` | `or` |
| `panjabi` | `pa` |
| `pilipino` | `fil` |
| `pushto` | `ps` |
| `putonghua` | `zh` |
| `scots gaelic` | `gd` |
| `sesotho` | `st` |
| `setswana` | `tn` |
| `shanghainese` | `wuu` |
| `sinhalese` | `si` |
| `sorani` | `ckb` |
| `standard chinese` | `zh` |
| `swiss high german` | `de-ch` |
| `taiwanese hokkien` | `nan` |
| `twi` | `ak` |
| `uighur` | `ug` |
| `valencian` | `ca` |

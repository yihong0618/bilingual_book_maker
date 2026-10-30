-- An image that stands alone in its paragraph is a figure: it gets the
-- class `bbm-figure`, which epub.css centres. Decided on Pandoc's own
-- tree, so an image inside a sentence is never touched (CSS cannot tell:
-- `p > img:only-child` ignores the text around the image).
function Para(para)
  if #para.content == 1 and para.content[1].t == "Image" then
    para.content[1].classes:insert("bbm-figure")
    return para
  end
end

-- Raw HTML is copied into the XHTML verbatim, so a void element in its
-- HTML spelling (`<br>`, which the preflight accepts and Datalab writes
-- inside table cells) leaves the document not well-formed XML. Written
-- self-closed instead; raw HTML only, so code is never touched.
local VOID = { br = true, hr = true, wbr = true }

function RawInline(raw)
  if raw.format ~= "html" then
    return nil
  end
  local name, rest = raw.text:match("^<%s*(%a+)(.-)/?>$")
  if name and VOID[name:lower()] then
    raw.text = "<" .. name:lower() .. rest:gsub("%s+$", "") .. " />"
    return raw
  end
end

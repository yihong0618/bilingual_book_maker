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

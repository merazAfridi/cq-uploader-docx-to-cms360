# DOCX to CMS360

Automated CQ uploading for 10 Minute School. Mark the questions in a Word file, get one image per question, check the marks, and upload them to CMS360 as children of a parent group question.

## What it does

1. **Upload a .docx.** Type `Q1` on its own line above the first question, `Q2` above the second, and so on. Each question runs down to the next marker; an optional `End` line stops the last one.
2. **Review.** Every question is rendered as an image, the way it looks in Word (fonts, tables, pictures). Marks are read from the heading bracket, such as `(১+১+৮ = ১০)`. A question with no marks gets a red box to fill in.
3. **Send to CMS360.** For each question the tool:
   - uploads the image and puts it inside the child's **Paragraph** box,
   - creates the child group question and links it to the parent at order index *N* (min = max = *N*, marks = the question's marks),
   - creates an **m9 · Only Question Text** question inside the child ("প্রশ্নগুলোর উত্তর দাও" by default) at order 1 (min = max = 1, same marks).

   Questions already linked to the parent are skipped, so running it twice never creates duplicates.

You can also download every image as one ZIP (`Ques 1.png`, `Ques 2.png`, …), or pull the embedded pictures out of a file.

## Run it

Requires Python 3.10+ and an internet connection (the page loads its libraries and fonts from CDNs). No packages to install.

```bash
python server.py            # then open http://127.0.0.1:8000
python server.py --port 8080
```

`web/index.html` also works when opened on its own for making images and the ZIP. Uploading to CMS360 needs `server.py`.

## CMS360 token

Paste it into the **CMS360 token** box on the page; it's saved locally in `secrets/.env`, which is git-ignored. To get one: in Chrome on CMS360, press F12, go to **Network**, click any `api.10minuteschool.com` request, and copy what follows `authorization: Bearer`. Chrome swaps its token every 10–30 minutes, so copy a fresh one right before uploading.

## Files

| Path | What it is |
|---|---|
| `web/index.html` | The whole web app: reads the .docx, renders and captures each question, marks, ZIP, upload UI |
| `server.py` | Local server: serves the page and makes the CMS360 API calls with the token from `secrets/.env` |
| `secrets/.env.example` | Template for `secrets/.env` |
| `output/` | Created at runtime: a copy of each uploaded image and `upload-log.csv` (git-ignored) |

## Notes

- Images use the fonts in the Word file, taken from the computer running the page. If a font isn't installed, the image uses a fallback. Save the .docx with **File → Options → Save → Embed fonts in the file** to avoid this.
- Old `.doc` files aren't supported; save as `.docx` first. For a Google Doc, use File → Download → Microsoft Word (.docx).

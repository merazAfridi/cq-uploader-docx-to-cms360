# CQ Uploader: DOCX to CMS360

A simple tool that uploads CQ questions to CMS360 straight from a Word file. Hours of manual work done in 5 minutes.

## How it works

1. **Mark the questions.** In your Word file, type `Q1` on its own line above the first question, `Q2` above the second, and so on.
2. **Upload the file.** Each question turns into an image: Ques 1, Ques 2, Ques 3…
3. **Check the marks.** Marks are picked up from the question heading, like (১+১+৮ = ১০). If a question has none, type them in.
4. **Add your CMS360 token and the parent question ID.**
5. **Click Upload.** Each question goes under the parent as a child, with its image, order number and marks, plus an m9 question inside.

Questions already under the parent are skipped, so nothing gets added twice.

Internal tool for 10 Minute School.

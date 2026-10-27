# kenne-chat-ui
This is a desktop chat UI project. It looks simple, but it has lots of details and feels complete. Built with Python and PySide6. It's just one file. Want something simple, stylish, and convenient? Try it.

## Download

Packaged Windows build, no Python required: **[KEnne.exe](https://github.com/KEnne0617/kenne-chat-ui/releases/latest/download/KEnne.exe)**

Run from source:

```bash
pip install PySide6
python src/KEnne_chat_UI.py
```

## Interface

Color: black, white, and gray.

Page border: subtle rounded corners.

Message border: 3 rounded corners, 1 square corner.

Design philosophy: minimalist, premium, and composed.

## Detailing

- When the cursor is on the input field border, it changes into a double-headed arrow, guide users to drag the border.(within limits) Conversely, revert.

- When the cursor is on the input field, "Please enter content..." fades in, guide users to click. Conversely, fades out.

- When the cursor click is on the input field, a blinking caret appears, guide user to enter content. Conversely, revert.

- When the content is on the input field, "Please enter content..." disappears. Conversely, revert. "SEND" lights up, guide the user to send content. Conversely, revert.

- When the input field contains too much content, a scrollbar appears on the right, guide the user to scroll and read. Conversely, disappears.

- When there are too many messages, as above.

- When the content lines height exceeds the input field height, prioritize increasing the input field height. Conversely, decrease.

## Keywords

SEND: Enter

INSERTS A NEWLINE: Shift+Enter or Ctrl+Enter

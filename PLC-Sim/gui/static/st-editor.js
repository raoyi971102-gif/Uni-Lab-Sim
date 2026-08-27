// Structured Text textarea enhancements kept independent from project I/O.
"use strict";

(function initStructuredTextEditor(global) {
  const DEFAULT_TAB_SIZE = 4;
  const BLOCK_OPEN = /(?:\bTHEN|\bDO|\bOF)$/i;
  const DECLARATION_OPEN = /^(?:VAR(?:_(?:INPUT|OUTPUT|IN_OUT|TEMP|GLOBAL|STAT|EXTERNAL|ACCESS|CONFIG))?|STRUCT|UNION|REPEAT|PROGRAM\b.*|FUNCTION(?:_BLOCK)?\b.*)$/i;
  const BLOCK_BRANCH = /^(?:ELSE|ELSIF\b.*\bTHEN)$/i;
  const BLOCK_CLOSE = /^(?:END_(?:IF|FOR|WHILE|CASE|VAR|STRUCT|UNION|PROGRAM|FUNCTION|FUNCTION_BLOCK|REPEAT|TYPE)(?:\s*;)?|UNTIL\b.*)$/i;

  function lineStart(value, position) {
    return value.lastIndexOf("\n", Math.max(0, position - 1)) + 1;
  }

  function lineEnd(value, position) {
    const nextBreak = value.indexOf("\n", position);
    return nextBreak === -1 ? value.length : nextBreak;
  }

  function indentationOf(text) {
    return (text.match(/^[ \t]*/) || [""])[0];
  }

  function removeIndent(indentation, tabSize) {
    if (indentation.startsWith("\t")) return indentation.slice(1);
    const count = Math.min(tabSize, (indentation.match(/^ +/) || [""])[0].length);
    return indentation.slice(count);
  }

  function emitInput(editor, inputType) {
    let event;
    try {
      event = new InputEvent("input", { bubbles: true, inputType });
    } catch (_) {
      event = new Event("input", { bubbles: true });
    }
    editor.dispatchEvent(event);
  }

  function applyEdit(editor, from, to, replacement, selectionStart, selectionEnd, inputType) {
    const scrollTop = editor.scrollTop;
    const scrollLeft = editor.scrollLeft;
    editor.setRangeText(replacement, from, to, "start");
    editor.setSelectionRange(selectionStart, selectionEnd);
    editor.scrollTop = scrollTop;
    editor.scrollLeft = scrollLeft;
    emitInput(editor, inputType);
  }

  function selectedLineRange(editor) {
    const value = editor.value;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const from = lineStart(value, start);
    const endProbe = end > start && value[end - 1] === "\n" ? end - 1 : end;
    return { from, to: lineEnd(value, endProbe), start, end };
  }

  function indentSelectedLines(editor, outdent, tabSize) {
    const range = selectedLineRange(editor);
    const original = editor.value.slice(range.from, range.to);
    const indent = " ".repeat(tabSize);
    const replacement = original.split("\n").map(line => {
      if (outdent) {
        if (line.startsWith("\t")) return line.slice(1);
        return line.replace(new RegExp(`^ {1,${tabSize}}`), "");
      }
      return line.length ? indent + line : line;
    }).join("\n");
    applyEdit(
      editor,
      range.from,
      range.to,
      replacement,
      range.from,
      range.from + replacement.length,
      outdent ? "deleteContentBackward" : "insertText",
    );
  }

  function insertTab(editor, tabSize) {
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    if (start !== end) {
      indentSelectedLines(editor, false, tabSize);
      return;
    }
    const from = lineStart(editor.value, start);
    const columnText = editor.value.slice(from, start);
    let column = 0;
    for (const character of columnText) {
      column += character === "\t" ? tabSize - (column % tabSize) : 1;
    }
    const spaces = " ".repeat(tabSize - (column % tabSize));
    applyEdit(editor, start, end, spaces, start + spaces.length, start + spaces.length, "insertText");
  }

  function outdentAtCaret(editor, tabSize) {
    const caret = editor.selectionStart;
    const from = lineStart(editor.value, caret);
    const to = lineEnd(editor.value, caret);
    const original = editor.value.slice(from, to);
    const indentation = indentationOf(original);
    const updatedIndentation = removeIndent(indentation, tabSize);
    const removed = indentation.length - updatedIndentation.length;
    if (!removed) return;
    const replacement = updatedIndentation + original.slice(indentation.length);
    const nextCaret = Math.max(from, caret - removed);
    applyEdit(editor, from, to, replacement, nextCaret, nextCaret, "deleteContentBackward");
  }

  function insertSmartNewline(editor, tabSize) {
    const value = editor.value;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const from = lineStart(value, start);
    const beforeCaret = value.slice(from, start);
    const indentation = indentationOf(beforeCaret);
    const code = beforeCaret.slice(indentation.length).replace(/\/\/.*$/, "").trim();
    const isBranch = BLOCK_BRANCH.test(code);
    const isCloser = BLOCK_CLOSE.test(code);
    let currentIndent = indentation;
    let editFrom = start;
    let currentLine = "";

    // A closing or branch keyword typed on an inherited indent is aligned first.
    if ((isBranch || isCloser) && indentation.length) {
      currentIndent = removeIndent(indentation, tabSize);
      editFrom = from;
      currentLine = currentIndent + beforeCaret.slice(indentation.length);
    }

    const opensBlock = BLOCK_OPEN.test(code) || DECLARATION_OPEN.test(code) || isBranch;
    const nextIndent = currentIndent + (opensBlock && !isCloser ? " ".repeat(tabSize) : "");
    const replacement = currentLine + "\n" + nextIndent;
    const caret = editFrom + replacement.length;
    applyEdit(editor, editFrom, end, replacement, caret, caret, "insertLineBreak");
  }

  function toggleLineComments(editor) {
    const range = selectedLineRange(editor);
    const original = editor.value.slice(range.from, range.to);
    const lines = original.split("\n");
    const contentLines = lines.filter(line => line.trim().length);
    const shouldUncomment = contentLines.length > 0 && contentLines.every(line => /^\s*\/\//.test(line));
    const replacement = lines.map(line => {
      if (!line.trim()) return line;
      if (shouldUncomment) return line.replace(/^(\s*)\/\/[ ]?/, "$1");
      return line.replace(/^(\s*)/, "$1// ");
    }).join("\n");

    if (range.start === range.end) {
      const delta = replacement.length - original.length;
      const caret = Math.max(range.from, range.start + delta);
      applyEdit(editor, range.from, range.to, replacement, caret, caret, "insertText");
      return;
    }
    applyEdit(editor, range.from, range.to, replacement, range.from, range.from + replacement.length, "insertText");
  }

  function handleKeydown(event, tabSize) {
    const editor = event.currentTarget;
    if (event.key === "Tab") {
      event.preventDefault();
      if (event.shiftKey) {
        if (editor.selectionStart === editor.selectionEnd) outdentAtCaret(editor, tabSize);
        else indentSelectedLines(editor, true, tabSize);
      } else {
        insertTab(editor, tabSize);
      }
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      insertSmartNewline(editor, tabSize);
      return;
    }
    if (event.key === "/" && (event.ctrlKey || event.metaKey) && !event.altKey) {
      event.preventDefault();
      toggleLineComments(editor);
    }
  }

  function attach(editor, options = {}) {
    if (!editor || editor.dataset.stEditorReady === "true") return;
    const tabSize = Number(options.tabSize || editor.dataset.tabSize || DEFAULT_TAB_SIZE);
    editor.dataset.stEditorReady = "true";
    editor.style.tabSize = String(tabSize);
    editor.addEventListener("keydown", event => handleKeydown(event, tabSize));
  }

  global.StructuredTextEditor = Object.freeze({ attach });
  document.querySelectorAll("textarea[data-st-editor]").forEach(editor => attach(editor));
})(window);

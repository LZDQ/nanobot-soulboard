function normalizeInlineMath(line: string): string {
  return line.replace(/\\\((.+?)\\\)/g, (_match, inner: string) => `$${inner}$`);
}

export function normalizeMathDelimiters(content: string): string {
  const lines = content.split(/\r?\n/);
  const normalized: string[] = [];
  let displayIndent = "";
  let inDisplayMath = false;

  for (const line of lines) {
    if (!inDisplayMath) {
      const openMatch = line.match(/^(\s*)\\\[\s*$/);
      if (openMatch) {
        displayIndent = openMatch[1];
        inDisplayMath = true;
        normalized.push(`${displayIndent}$$`);
        continue;
      }

      normalized.push(normalizeInlineMath(line));
      continue;
    }

    const closeMatch = line.match(/^(\s*)\\\]\s*$/);
    if (closeMatch) {
      normalized.push(`${displayIndent}$$`);
      displayIndent = "";
      inDisplayMath = false;
      continue;
    }

    normalized.push(line);
  }

  return normalized.join("\n");
}

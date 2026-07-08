import { isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { toast } from "sonner";
import "katex/dist/katex.min.css";

import { copyToClipboard } from "../lib/clipboard";
import { notifyError } from "../lib/errors";
import { normalizeMathDelimiters } from "../markdown";

function extractCode(children: ReactNode): string {
  const child = Array.isArray(children) ? children[0] : children;
  if (!isValidElement<{ children?: ReactNode }>(child)) {
    return "";
  }
  const code = child.props.children;
  if (Array.isArray(code)) {
    return code.join("").replace(/\n$/, "");
  }
  return String(code ?? "").replace(/\n$/, "");
}

export function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          pre: ({ children }) => {
            const code = extractCode(children);
            return (
              <div className="markdown-code-block">
                <button
                  type="button"
                  className="ghost markdown-code-copy"
                  onClick={() => {
                    void copyToClipboard(code).then(() => {
                      toast.success("Copied code");
                    }).catch((cause) => {
                      notifyError(cause);
                    });
                  }}
                  disabled={!code}
                >
                  Copy
                </button>
                <pre>{children}</pre>
              </div>
            );
          },
        }}
      >
        {normalizeMathDelimiters(content)}
      </ReactMarkdown>
    </div>
  );
}

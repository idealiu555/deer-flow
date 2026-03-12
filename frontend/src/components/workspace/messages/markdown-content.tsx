"use client";

import { Children, isValidElement, useMemo } from "react";
import type { HTMLAttributes } from "react";
import type { ReactNode } from "react";

import {
  MessageResponse,
  type MessageResponseProps,
} from "@/components/ai-elements/message";
import { streamdownPlugins } from "@/core/streamdown";

import { CitationLink } from "../citations/citation-link";

export type MarkdownContentProps = {
  content: string;
  isLoading: boolean;
  rehypePlugins: MessageResponseProps["rehypePlugins"];
  className?: string;
  remarkPlugins?: MessageResponseProps["remarkPlugins"];
  components?: MessageResponseProps["components"];
};

type MarkdownParagraphProps = HTMLAttributes<HTMLParagraphElement> & {
  children?: ReactNode;
  node?: unknown;
};

const BLOCK_TAGS = new Set([
  "address",
  "article",
  "aside",
  "blockquote",
  "details",
  "dialog",
  "div",
  "dl",
  "fieldset",
  "figcaption",
  "figure",
  "footer",
  "form",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "header",
  "hr",
  "main",
  "nav",
  "ol",
  "p",
  "pre",
  "section",
  "table",
  "ul",
]);

const BLOCK_STREAMDOWN_NODES = new Set([
  "code-block",
  "mermaid-block",
  "table-wrapper",
]);

function hasBlockDescendant(node: ReactNode): boolean {
  return Children.toArray(node).some((child) => {
    if (!isValidElement<Record<string, unknown>>(child)) {
      return false;
    }

    const childProps = child.props as {
      children?: ReactNode;
      "data-streamdown"?: unknown;
    };

    if (typeof child.type === "string" && BLOCK_TAGS.has(child.type)) {
      return true;
    }

    const dataStreamdown = childProps["data-streamdown"];
    if (
      typeof dataStreamdown === "string" &&
      BLOCK_STREAMDOWN_NODES.has(dataStreamdown)
    ) {
      return true;
    }

    if (childProps.children) {
      return hasBlockDescendant(childProps.children);
    }

    return false;
  });
}

function SafeParagraph({ children, node: _node, ...props }: MarkdownParagraphProps) {
  if (hasBlockDescendant(children)) {
    return <div {...props}>{children}</div>;
  }

  return <p {...props}>{children}</p>;
}

/** Renders markdown content. */
export function MarkdownContent({
  content,
  rehypePlugins,
  className,
  remarkPlugins = streamdownPlugins.remarkPlugins,
  components: componentsFromProps,
}: MarkdownContentProps) {
  const components = useMemo(() => {
    return {
      p: SafeParagraph,
      a: (props: HTMLAttributes<HTMLAnchorElement>) => {
        if (typeof props.children === "string") {
          const match = /^citation:(.+)$/.exec(props.children);
          if (match) {
            const [, text] = match;
            return <CitationLink {...props}>{text}</CitationLink>;
          }
        }
        return <a {...props} />;
      },
      ...componentsFromProps,
    };
  }, [componentsFromProps]);

  if (!content) return null;

  return (
    <MessageResponse
      className={className}
      remarkPlugins={remarkPlugins}
      rehypePlugins={rehypePlugins}
      components={components}
    >
      {content}
    </MessageResponse>
  );
}

import { ArrowUp, Check, ChevronDown, FilePen, Shield, Terminal, Wrench } from "lucide-react";

import { useSiteCopy } from "@/lib/site-copy";

/** What the sketch shows: a model from a free station, with three tools on. */
const PREVIEW_MODEL = "qwen3-coder";

/** Hoshimi's mark, as the Web UI and the favicon draw it: a guiding star and three nodes. */
function HoshimiMark() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 10l1.6-5.6M12 18l-7.6 1.7M12 18l7.6 1.7" />
      <path d="M12 2l1.8 6.2L19 10l-5.2 1.8L12 18l-1.8-6.2L5 10l5.2-1.8L12 2Z" fill="currentColor" />
      <circle cx="21" cy="3" r="1.4" />
      <circle cx="3" cy="20" r="1.4" />
      <circle cx="21" cy="20" r="1.4" />
    </svg>
  );
}

/** A static sketch of the Playground: a question, the tools the model called, its answer and the composer. */
export default function ProductWorkspacePreview() {
  const copy = useSiteCopy().home.preview;

  return (
    <figure className="product-preview page-interactive" aria-label={copy.label}>
      <div className="product-preview-window page-interactive-part">
        <div className="product-preview-titlebar">
          <div className="product-preview-window-controls" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div className="product-preview-brand">
            <span className="product-preview-brand-mark" aria-hidden="true">
              <HoshimiMark />
            </span>
            Hoshimi
          </div>
          <div className="product-preview-titlebar-title">{copy.title}</div>
        </div>

        <div className="product-preview-body">
          <p className="product-preview-bubble">{copy.question}</p>
          <div className="product-preview-tools">
            {copy.tools.map(({ verb, detail, time }, index) => {
              const Icon = index === 0 ? Terminal : FilePen;
              return (
                <div className="product-preview-tool" key={detail}>
                  <Icon aria-hidden="true" />
                  <strong>{verb}</strong>
                  <code>{detail}</code>
                  <span className="product-preview-tool-state">
                    <Check aria-hidden="true" />
                    {time}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="product-preview-answer">{copy.answer}</p>
          <div className="product-preview-composer">
            <span>{copy.placeholder}</span>
            <div className="product-preview-composer-bar">
              <span className="product-preview-chip">
                {PREVIEW_MODEL}
                <ChevronDown aria-hidden="true" />
              </span>
              <span className="product-preview-chip">
                <Wrench aria-hidden="true" />3
              </span>
              <span className="product-preview-chip is-optional">
                <Shield aria-hidden="true" />
                {copy.approval}
              </span>
              <span className="product-preview-send" aria-hidden="true">
                <ArrowUp />
              </span>
            </div>
          </div>
        </div>
      </div>
    </figure>
  );
}

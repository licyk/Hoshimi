import { createFileRoute } from "@tanstack/react-router";
import { HomeLayout } from "fumadocs-ui/layouts/home";
import { useRef } from "react";

import FeaturesSection from "@/components/home/features-section";
import HeroSection from "@/components/home/hero-section";
import HighlightsSection from "@/components/home/highlights-section";
import InstallSection from "@/components/home/install-section";
import WorkflowSection from "@/components/home/workflow-section";
import SectionRail from "@/components/page/section-rail";
import { useDocumentMetadata } from "@/lib/document-metadata";
import { HOME_SECTION_IDS, homeSections } from "@/lib/home-sections";
import type { Locale } from "@/lib/i18n";
import { baseOptions } from "@/lib/layout.shared";
import { canonicalUrl } from "@/lib/site-config";
import { useActiveSection, useScrollReveal } from "@/lib/page-motion";
import { useSiteLocale } from "@/lib/site-copy";

const METADATA: Record<Locale, { description: string; title: string }> = {
  "zh-CN": {
    title: "Hoshimi — 兼容 OpenAI 与 Anthropic 的模型网关",
    description:
      "把免费模型站点、云服务与本地模型汇聚到同一个 API 与网页界面：Chat Completions、Responses 与 Anthropic Messages，账号自动登录、续期与轮换，带有能使用工具的 Playground。",
  },
  en: {
    title: "Hoshimi — an OpenAI- and Anthropic-compatible model gateway",
    description:
      "Free model stations, cloud services and local models behind one API and one web UI: Chat Completions, Responses and Anthropic Messages, accounts signed in, renewed and rotated for you, and a Playground with tools.",
  },
};

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: METADATA["zh-CN"].title },
      { name: "description", content: METADATA["zh-CN"].description },
    ],
    links: [{ rel: "canonical", href: canonicalUrl("/") }],
  }),
  component: Home,
});

function Home() {
  const locale = useSiteLocale();
  const homeRef = useRef<HTMLElement>(null);
  const sections = homeSections(locale);
  const activeSectionId = useActiveSection(HOME_SECTION_IDS);

  useDocumentMetadata(METADATA[locale]);
  useScrollReveal(homeRef);

  return (
    <HomeLayout {...baseOptions(locale)} className="home-page paged" ref={homeRef}>
      <SectionRail activeId={activeSectionId} sections={sections} />
      <HeroSection />
      <FeaturesSection />
      <WorkflowSection />
      <HighlightsSection />
      <InstallSection />
    </HomeLayout>
  );
}

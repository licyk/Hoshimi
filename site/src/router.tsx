import { createRouter } from "@tanstack/react-router";

import { NotFound } from "@/components/not-found";
import { routerBasePath } from "@/lib/i18n";
import { routeTree } from "@/routeTree.gen";
import { routeStaticCacheUnderBase } from "@/lib/static-cache";

export function getRouter() {
  routeStaticCacheUnderBase();
  return createRouter({
    routeTree,
    basepath: routerBasePath(import.meta.env.BASE_URL),
    defaultPreload: "intent",
    scrollRestoration: true,
    defaultNotFoundComponent: NotFound,
  });
}

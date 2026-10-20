import type { Metadata } from "next";
import { Suspense } from "react";
import { Explore } from "@/components/explore";
import { LoadingCards } from "@/components/ui";

export const metadata: Metadata = { title: "探索模组" };
export default function Page() { return <Suspense fallback={<LoadingCards/>}><Explore/></Suspense>; }

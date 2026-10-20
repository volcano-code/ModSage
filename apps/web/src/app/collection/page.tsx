import type { Metadata } from "next";
import { Collection } from "@/components/collection";

export const metadata: Metadata = { title: "我的模组清单" };
export default function Page() { return <Collection/>; }

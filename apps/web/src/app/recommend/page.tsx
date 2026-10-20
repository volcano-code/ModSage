import type { Metadata } from "next";
import { Recommend } from "@/components/recommend";

export const metadata: Metadata = { title: "描述需求，寻找模组" };
export default function Page() { return <Recommend/>; }

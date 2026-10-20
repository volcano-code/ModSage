import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Detail } from "@/components/detail";

export const metadata: Metadata = { title: "模组详情与依据" };
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d{1,20}$/.test(id)) notFound();
  return <Detail id={id}/>;
}

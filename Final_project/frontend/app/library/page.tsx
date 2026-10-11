import type { Metadata } from "next";
import LibraryApp from "@/components/library/LibraryApp";

export const metadata: Metadata = { title: "Document library — Research Assistant" };

export default function LibraryPage() {
  return <LibraryApp />;
}

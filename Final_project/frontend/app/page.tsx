import ResearchApp from "@/components/ResearchApp";

/** `?chat=conv_…` opens that chat (loaded from the server when this browser does not know
 * it); `&run=run_…` (or `?run=` alone, the links Telegram and callbacks send) opens the
 * chat of that run at its turn. searchParams is a Promise in Next 16. */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { chat, run } = await searchParams;
  return (
    <ResearchApp
      initialChatId={typeof chat === "string" ? chat : null}
      initialRunId={typeof run === "string" ? run : null}
    />
  );
}

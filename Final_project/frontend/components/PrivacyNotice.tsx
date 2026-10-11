export default function PrivacyNotice() {
  return (
    <p className="text-xs text-zinc-600 dark:text-zinc-400">
      <strong className="font-medium">Privacy:</strong> your questions, uploaded documents and the web pages the
      agents read are sent to Google Gemini (free tier); search queries go to web search engines and Wikipedia.
      Runs are kept for 30 days and anyone with a run link can open it; uploads expire after 24 hours. Don&apos;t
      include personal data or secrets.
    </p>
  );
}

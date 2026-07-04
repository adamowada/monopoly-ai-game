import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Monopoly AI Game Console",
  description: "Local operational console shell for the Monopoly AI research game.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

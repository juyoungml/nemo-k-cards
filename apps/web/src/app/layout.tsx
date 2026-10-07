import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Sans_KR } from "next/font/google";

import { Sidebar } from "@/components/sidebar";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const notoKr = Noto_Sans_KR({
  variable: "--font-noto-kr",
  weight: ["400", "700"],
  preload: false,
});

export const metadata: Metadata = {
  title: "What's On Korea · Admin",
  description: "Research, verify and publish Korean event card news for foreigners.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${notoKr.variable} h-full antialiased`}
    >
      <body className="flex min-h-full bg-canvas">
        <Sidebar />
        <main className="min-w-0 flex-1 space-y-6 px-10 py-8">{children}</main>
      </body>
    </html>
  );
}

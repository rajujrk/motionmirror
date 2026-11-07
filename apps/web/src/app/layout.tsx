import type { Metadata } from "next";
import { Anybody, Hanken_Grotesk } from "next/font/google";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { Providers } from "./providers";
import "./globals.css";

const anybody = Anybody({ subsets: ["latin"], variable: "--font-anybody", axes: ["wdth"] });
const hanken = Hanken_Grotesk({ subsets: ["latin"], variable: "--font-hanken" });

export const metadata: Metadata = {
  title: "MotionMirror",
  description: "Put yourself in a dance video.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Every page is rendered per request, so the CSP nonce is fresh and nothing private is cached.
  await connection();
  return (
    <html lang="en" className={`${anybody.variable} ${hanken.variable}`}>
      <body className="min-h-dvh">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { TapeX } from "@/components/ui";
import { api } from "@/lib/api";
import { useMe } from "@/lib/hooks";

export function AppHeader() {
  const me = useMe();
  const [csrf, setCsrf] = useState<string>();

  useEffect(() => {
    api<{ token: string }>("/api/v1/auth/csrf")
      .then((result) => setCsrf(result.token))
      .catch(() => setCsrf(undefined));
  }, []);

  return (
    <header className="flex items-center justify-between gap-4 border-b border-rule bg-sheen px-4 py-2 sm:px-6">
      <Link href="/projects" className="flex items-center gap-2">
        <TapeX size={20} />
        <span className="mark-heading text-2xl">MotionMirror</span>
      </Link>
      <div className="flex items-center gap-4 text-sm">
        {me.data ? <span className="hidden text-ink-soft sm:inline">{me.data.email}</span> : null}
        <form method="post" action="/logout">
          <input type="hidden" name="_csrf" value={csrf ?? ""} />
          <button type="submit" disabled={!csrf} className="underline underline-offset-3 hover:text-tape-deep">
            Sign out
          </button>
        </form>
      </div>
    </header>
  );
}

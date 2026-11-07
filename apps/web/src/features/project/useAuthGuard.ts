"use client";

import { useEffect } from "react";
import { ApiError, SIGN_IN_URL } from "@/lib/api";
import { useMe } from "@/lib/hooks";

/** The API decides who is signed in. This only sends the browser to the login page when it says no. */
export function useAuthGuard() {
  const me = useMe();
  const signedOut = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (signedOut) window.location.assign(SIGN_IN_URL);
  }, [signedOut]);
  return { ready: Boolean(me.data) };
}

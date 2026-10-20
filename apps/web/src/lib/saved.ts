"use client";

import { useSyncExternalStore } from "react";
import type { SavedMod } from "./types";

const KEY = "modsage.saved.v1";
const EVENT = "modsage-saved-change";
const EMPTY: SavedMod[] = [];
let cachedRaw: string | null | undefined;
let cachedValue: SavedMod[] = EMPTY;

function snapshot(): SavedMod[] {
  let raw: string | null;
  try { raw = window.localStorage.getItem(KEY); } catch { return EMPTY; }
  if (raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  try {
    const value: unknown = JSON.parse(raw ?? "[]");
    cachedValue = Array.isArray(value) ? value.filter((item): item is SavedMod =>
      typeof item === "object" && item !== null && typeof item.id === "string" && /^\d{1,20}$/.test(item.id) && typeof item.title === "string",
    ).slice(0, 100).map((item) => ({ id: item.id, title: item.title.slice(0, 300) })) : EMPTY;
  } catch { cachedValue = EMPTY; }
  return cachedValue;
}

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(EVENT, callback);
  return () => { window.removeEventListener("storage", callback); window.removeEventListener(EVENT, callback); };
}

export function useSavedMods() { return useSyncExternalStore(subscribe, snapshot, () => EMPTY); }

export function toggleSaved(mod: SavedMod): boolean {
  const list = snapshot();
  const next = list.some((item) => item.id === mod.id) ? list.filter((item) => item.id !== mod.id) : [...list, { id: mod.id, title: mod.title }];
  if (next.length > 100) return false;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(EVENT));
    return true;
  } catch { return false; }
}

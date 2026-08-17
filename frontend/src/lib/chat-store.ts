import { create } from "zustand";
import type { ChatResponse, ChatMode, ChatIntent, BestOf, ChatRequestParams } from "@/lib/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  query?: string;
  response?: ChatResponse;
  pending?: boolean;
  error?: string;
}

interface ChatStoreState {
  messages: ChatMessage[];
  sessionId: string;
  addUserMessage: (query: string) => string;
  addPendingAssistant: (query: string) => string;
  resolveAssistant: (id: string, response: ChatResponse) => void;
  failAssistant: (id: string, error: string) => void;
  retryAssistant: (id: string) => void;
  clear: () => void;
  loadMessages: (messages: ChatMessage[]) => void;

  mode: ChatMode;
  setMode: (mode: ChatMode) => void;

  intent: ChatIntent;
  setIntent: (intent: ChatIntent) => void;

  bestOf: BestOf;
  setBestOf: (bestOf: BestOf) => void;

  corpusId: string;
  setCorpusId: (corpusId: string) => void;

  activePatientCorpusId: string | null;
  setActivePatientCorpusId: (id: string | null) => void;

  // Which patient is selected in the Knowledge panel (independent of corpus routing).
  selectedPatientId: string | null;
  setSelectedPatientId: (id: string | null) => void;
}

let counter = 0;
function nextId() {
  counter += 1;
  return `msg_${Date.now()}_${counter}`;
}

function nextSessionId() {
  return `session_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

export const useChatStore = create<ChatStoreState>((set) => ({
  messages: [],
  sessionId: nextSessionId(),

  addUserMessage: (query) => {
    const id = nextId();
    set((s) => ({ messages: [...s.messages, { id, role: "user", query }] }));
    return id;
  },

  addPendingAssistant: (query) => {
    const id = nextId();
    set((s) => ({ messages: [...s.messages, { id, role: "assistant", query, pending: true }] }));
    return id;
  },

  resolveAssistant: (id, response) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, pending: false, response } : m)),
    })),

  failAssistant: (id, error) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, pending: false, error } : m)),
    })),

  retryAssistant: (id) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, pending: true, error: undefined, response: undefined } : m
      ),
    })),

  clear: () => set({ messages: [], sessionId: nextSessionId() }),
  loadMessages: (messages) => set({ messages, sessionId: nextSessionId() }),

  mode: "concise",
  setMode: (mode) => set({ mode }),

  intent: "auto",
  setIntent: (intent) => set({ intent }),

  bestOf: 3,
  setBestOf: (bestOf) => set({ bestOf }),

  corpusId: "default",
  setCorpusId: (corpusId) => set({ corpusId }),

  activePatientCorpusId: null,
  setActivePatientCorpusId: (id) => set({ activePatientCorpusId: id }),

  selectedPatientId: null,
  setSelectedPatientId: (id) => set({ selectedPatientId: id }),
}));

export function buildChatParams(
  mode: ChatMode,
  intent: ChatIntent,
  bestOf: BestOf,
  corpusId: string,
  activePatientCorpusId: string | null,
): Omit<ChatRequestParams, "query"> {
  const base = intent !== "auto"
    ? { best_of: bestOf, intent, raw: true }
    : { best_of: bestOf, mode };
  return {
    ...base,
    corpus_id: corpusId,
    active_patient_corpus_id: activePatientCorpusId ?? undefined,
  };
}

import { TapeX } from "@/components/ui";
import { SIGN_IN_URL } from "@/lib/api";

export default function HomePage() {
  return (
    <main className="grid min-h-dvh grid-rows-[1fr_auto] bg-floor text-sheen">
      <section className="relative flex flex-col justify-end overflow-hidden px-5 pb-10 pt-24 sm:px-10">
        <TapeX size={140} className="absolute right-[12%] top-[14%] rotate-6" />
        <TapeX size={72} color="#ffc91f" className="absolute right-[34%] top-[46%] -rotate-12" />
        <TapeX size={96} color="#7ad6e0" className="absolute left-[8%] top-[20%] rotate-12" />
        <h1 className="mark-heading max-w-[12ch] text-[clamp(4rem,15vw,12rem)]">Put yourself in the dance.</h1>
        <p className="mt-6 max-w-[52ch] text-lg text-sheen/85">
          Upload a dance clip, mark the dancer, and add a photo of yourself. MotionMirror generates a new video with you
          in their place, on their beat.
        </p>
        <a
          href={SIGN_IN_URL}
          className="mt-8 w-fit rounded-sm bg-tape px-6 py-3 text-lg font-semibold text-white hover:bg-tape-deep"
        >
          Sign in
        </a>
      </section>
      <footer className="px-5 py-4 text-sm text-sheen/70 sm:px-10">
        Private to your account. Results are AI-edited, and hands, feet and spins may not come out exact.
      </footer>
    </main>
  );
}

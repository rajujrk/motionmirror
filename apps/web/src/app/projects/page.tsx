"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, ErrorNote, Hint, TapeX } from "@/components/ui";
import { AppHeader } from "@/features/project/AppHeader";
import { useAuthGuard } from "@/features/project/useAuthGuard";
import { api, ApiError } from "@/lib/api";
import { useProjects } from "@/lib/hooks";
import type { Project } from "@/lib/types";

const schema = z.object({ name: z.string().trim().min(1, "Give the project a name").max(200) });
type FormValues = z.infer<typeof schema>;

export default function ProjectsPage() {
  const { ready } = useAuthGuard();
  const projects = useProjects();
  const client = useQueryClient();
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { name: "" } });
  const create = useMutation({
    mutationFn: (values: FormValues) => api<Project>("/api/v1/projects", { method: "POST", body: values }),
    onSuccess: () => {
      form.reset();
      return client.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  if (!ready) return null;
  const items = projects.data?.items ?? [];

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <h1 className="mark-heading text-5xl sm:text-6xl">Projects</h1>

        <form
          onSubmit={form.handleSubmit((values) => create.mutate(values))}
          className="mt-6 flex flex-wrap items-start gap-3"
          noValidate
        >
          <div className="min-w-60 flex-1">
            <label htmlFor="project-name" className="mb-1 block font-semibold">
              New project name
            </label>
            <input
              id="project-name"
              className="w-full rounded-sm border border-ink bg-sheen px-3 py-2"
              aria-invalid={Boolean(form.formState.errors.name)}
              {...form.register("name")}
            />
            {form.formState.errors.name ? (
              <p className="mt-1 text-sm text-tape-deep">{form.formState.errors.name.message}</p>
            ) : null}
          </div>
          <Button type="submit" variant="primary" className="mt-7" disabled={create.isPending}>
            Create project
          </Button>
        </form>
        {create.error instanceof ApiError ? <ErrorNote>{create.error.message}</ErrorNote> : null}

        {projects.error ? <ErrorNote>Projects couldn&apos;t load. Reload the page to try again.</ErrorNote> : null}

        {projects.isSuccess && items.length === 0 ? (
          <div className="mt-10 flex items-center gap-3">
            <TapeX size={28} />
            <Hint>No projects yet. Name one above, then upload a dance clip.</Hint>
          </div>
        ) : null}

        <ul className="mt-8 divide-y divide-rule border-y border-rule">
          {items.map((project) => (
            <li key={project.id}>
              <Link
                href={`/projects/${project.id}`}
                className="flex items-baseline justify-between gap-4 py-3 hover:text-tape-deep"
              >
                <span className="text-lg font-semibold">{project.name}</span>
                <span className="timecode text-sm text-ink-soft">
                  {new Date(project.createdAt).toLocaleDateString()}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </>
  );
}

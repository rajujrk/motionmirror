package com.motionmirror.jobs;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.identity.CurrentUserService;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

@RestController
@RequestMapping("/api/v1")
public class JobController {

    public record CreatePreprocessing(@NotNull UUID selectionId) {}

    public record CreateGeneration(@NotNull @Pattern(regexp = "PREVIEW|FULL") String kind, @NotNull UUID selectionId,
            @NotNull UUID appearanceProfileId, @NotNull UUID preprocessJobId, @NotNull @Size(max = 64) String presetId,
            JsonNode overrides, boolean consentConfirmed) {}

    public record Progress(String stage, Integer current, Integer total, String unit) {}

    public record OutputView(String name, String contentType, long sizeBytes, String sha256, JsonNode media) {}

    /** `outputLabel` says plainly whether a model produced this, so the UI can never imply otherwise. */
    public record JobView(UUID id, UUID projectId, String kind, String state, String stage, String backendId, String outputLabel,
            String configRevision, JsonNode requestedSettings, JsonNode effectiveSettings, JsonNode inputs,
            int attemptCount, int maxAttempts, Instant nextAttemptAt, String failureCode, String failureDetail,
            Instant createdAt, Instant updatedAt, Instant finishedAt, Progress progress, List<OutputView> outputs) {}

    public record JobList(List<JobView> items) {}

    public record EventView(long id, Instant occurredAt, String type, JsonNode detail) {}

    public record EventList(List<EventView> items) {}

    public record DownloadUrl(String url) {}

    private final JobService jobs;
    private final JobSubmissionService submissions;
    private final JobStore store;
    private final CurrentUserService currentUser;
    private final ObjectStorage storage;
    private final JdbcClient jdbc;

    public JobController(JobService jobs, JobSubmissionService submissions, JobStore store, CurrentUserService currentUser,
            ObjectStorage storage, JdbcClient jdbc) {
        this.jobs = jobs;
        this.submissions = submissions;
        this.store = store;
        this.currentUser = currentUser;
        this.storage = storage;
        this.jdbc = jdbc;
    }

    @PostMapping("/projects/{projectId}/preprocessing-jobs")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public JobView createPreprocessing(@PathVariable UUID projectId, @Valid @RequestBody CreatePreprocessing request,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return view(submissions.submitPreprocess(currentUser.require(), projectId, request.selectionId(), checkKey(idempotencyKey)));
    }

    @PostMapping("/projects/{projectId}/generation-jobs")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public JobView createGeneration(@PathVariable UUID projectId, @Valid @RequestBody CreateGeneration request,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return view(submissions.submitGeneration(currentUser.require(), projectId, new JobSubmissionService.GenerationRequest(
                JobKind.valueOf(request.kind()), request.selectionId(), request.appearanceProfileId(), request.preprocessJobId(),
                request.presetId(), request.overrides() == null ? null : request.overrides().toString(),
                request.consentConfirmed(), checkKey(idempotencyKey))));
    }

    private static String checkKey(String key) {
        if (key != null && !key.matches("^[A-Za-z0-9_-]{8,128}$")) {
            throw ApiException.badRequest("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 8 to 128 safe characters");
        }
        return key;
    }

    @GetMapping("/projects/{projectId}/jobs")
    public JobList list(@PathVariable UUID projectId, @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return new JobList(submissions.list(currentUser.require(), projectId, page, size).stream()
                .filter(job -> job.kind() != JobKind.VALIDATE).map(this::view).toList());
    }

    @GetMapping("/jobs/{id}")
    public JobView get(@PathVariable UUID id) {
        return view(jobs.requireOwned(currentUser.require(), id));
    }

    @GetMapping("/jobs/{id}/events")
    public EventList events(@PathVariable UUID id) {
        jobs.requireOwned(currentUser.require(), id);
        return new EventList(store.events(id).stream()
                .map(event -> new EventView(event.id(), event.occurredAt(), event.type(), Json.read(event.detail())))
                .toList());
    }

    @PostMapping("/jobs/{id}/cancel")
    public JobView cancel(@PathVariable UUID id) {
        return view(jobs.cancel(currentUser.require(), id));
    }

    @PostMapping("/jobs/{id}/retry")
    public JobView retry(@PathVariable UUID id) {
        return view(jobs.retry(currentUser.require(), id));
    }

    @GetMapping("/jobs/{id}/outputs/{name}/download-url")
    public DownloadUrl outputUrl(@PathVariable UUID id, @PathVariable @Pattern(regexp = "^[a-z0-9_.-]{1,64}$") String name) {
        CurrentUser user = currentUser.require();
        jobs.requireOwned(user, id);
        JobStore.OutputRow output = store.outputs(id).stream().filter(row -> row.name().equals(name)).findFirst()
                .orElseThrow(() -> ApiException.notFound("Output"));
        return new DownloadUrl(storage.presignBrowserDownload(output.objectKey(), name));
    }

    JobView view(JobRow job) {
        Progress progress = jdbc.sql("""
                SELECT progress::text FROM job_attempts WHERE job_id = :job ORDER BY attempt_no DESC LIMIT 1
                """).param("job", job.id()).query(String.class).optional()
                .map(Json::read)
                .filter(node -> node.has("stage"))
                .map(node -> new Progress(node.path("stage").asString(), intOrNull(node, "current"), intOrNull(node, "total"),
                        node.path("unit").isNull() ? null : node.path("unit").asString()))
                .orElse(null);
        List<OutputView> outputs = store.outputs(job.id()).stream()
                .map(row -> new OutputView(row.name(), row.contentType(), row.sizeBytes(), row.sha256(), Json.read(row.media())))
                .toList();
        return new JobView(job.id(), job.projectId(), job.kind().name(), job.state().name(), job.stage(), job.backendId(),
                JobRouting.MOCK.equals(job.backendId()) ? "mock" : "real", job.configRevision(), Json.read(job.requestedSettings()),
                Json.read(job.effectiveSettings()), Json.read(job.inputs()), job.attemptCount(), job.maxAttempts(),
                job.nextAttemptAt(), job.failureCode(), job.failureDetail(), job.createdAt(), job.updatedAt(), job.finishedAt(),
                progress, outputs);
    }

    private static Integer intOrNull(JsonNode node, String field) {
        return node.path(field).isNumber() ? node.path(field).asInt() : null;
    }
}

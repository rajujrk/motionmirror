package com.motionmirror.retention;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.oidcLogin;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.audit.AuditService;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobService;
import com.motionmirror.jobs.JobStore;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import com.motionmirror.support.Fixtures;
import com.motionmirror.support.IntegrationTest;
import com.motionmirror.workers.LeaseService;
import com.motionmirror.workers.WorkerDtos;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.OidcLoginRequestPostProcessor;
import org.springframework.test.web.servlet.MockMvc;

@IntegrationTest
class DeletionIT {

    @Autowired MockMvc mvc;
    @Autowired Fixtures fixtures;
    @Autowired DeletionService deletion;
    @Autowired LeaseService leases;
    @Autowired JobService jobService;
    @Autowired JobStore jobs;
    @Autowired JdbcClient jdbc;
    @Autowired ObjectStorage storage;
    @Autowired AuditService audit;
    @Autowired AppProperties properties;

    private StoragePurger purger(ObjectStorage target) {
        return new StoragePurger(jdbc, target, audit, deletion, properties);
    }

    private StoragePurger purger() {
        return purger(storage);
    }

    private static OidcLoginRequestPostProcessor login(String subject) {
        return oidcLogin().idToken(token -> token.subject(subject).claim("iss", "https://issuer.test"));
    }

    private static CurrentUser user(UUID id) {
        return new CurrentUser(id, Set.of("ROLE_USER"));
    }

    private UUID userId(String subject) {
        return jdbc.sql("SELECT id FROM users WHERE issuer = 'https://issuer.test' AND subject = :subject")
                .param("subject", subject).query(UUID.class).single();
    }

    private UUID createProject(String subject, String name) throws Exception {
        String body = mvc.perform(post("/api/v1/projects").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(Json.write(Map.of("name", name))))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return UUID.fromString(Json.read(body).path("id").asString());
    }

    private String put(String key) throws Exception {
        String url = storage.presignWorkerWrite(key, "video/mp4");
        HttpResponse<String> response = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(URI.create(url)).header("Content-Type", "video/mp4")
                        .PUT(HttpRequest.BodyPublishers.ofByteArray(new byte[] {1})).build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(response.statusCode()).isEqualTo(200);
        return key;
    }

    private JobRow job(UUID owner, UUID project, JobKind kind, Map<String, Object> inputs) {
        return jobService.submit(owner, new JobService.Submission(project, kind, "preview@2", "{}", "{\"seed\":1}",
                Json.write(inputs), null, true));
    }

    /** A job that ran: an attempt, a recorded output and a stray file the worker never reported. */
    private record Ran(JobRow job, WorkerDtos.AcquireResponse attempt, String reported, String unreported) {}

    private Ran ran(UUID owner, UUID project, JobKind kind, Map<String, Object> inputs) throws Exception {
        JobRow row = job(owner, project, kind, inputs);
        WorkerDtos.AcquireResponse attempt = ((LeaseService.Acquired) leases.acquire(row.id(), UUID.randomUUID(), 1, "w", Set.of(row.backendId()))).response();
        String prefix = LeaseService.attemptPrefix(attempt.attemptId());
        String reported = put(prefix + "result.mp4");
        String unreported = put(prefix + "partial.mp4");
        jobs.insertOutput(row.id(), attempt.attemptId(), "result.mp4", reported, "a".repeat(64), 1, "video/mp4", "{}");
        return new Ran(row, attempt, reported, unreported);
    }

    private long count(String table, String column, UUID id) {
        return jdbc.sql("SELECT count(*) FROM " + table + " WHERE " + column + " = :id").param("id", id).query(Long.class).single();
    }

    private UUID selection(UUID project, UUID owner, UUID source) {
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                INSERT INTO subject_selections (id, project_id, owner_id, source_asset_id, revision, frame_index, timestamp_num,
                    timestamp_den, coordinate_mapping, annotations)
                VALUES (:id, :project, :owner, :source, 1, 0, 0, 30, '{}', '{}')
                """).param("id", id).param("project", project).param("owner", owner).param("source", source).update();
        return id;
    }

    private UUID profile(UUID project, UUID owner, UUID primary, UUID extra) {
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO appearance_profiles (id, project_id, owner_id, name) VALUES (:id, :project, :owner, 'mine')")
                .param("id", id).param("project", project).param("owner", owner).update();
        for (UUID asset : extra == null ? List.of(primary) : List.of(primary, extra)) {
            jdbc.sql("""
                    INSERT INTO reference_images (id, profile_id, asset_id, view_label, is_primary)
                    VALUES (:id, :profile, :asset, 'FULL_BODY', :primary)
                    """).param("id", UUID.randomUUID()).param("profile", id).param("asset", asset)
                    .param("primary", asset.equals(primary)).update();
        }
        return id;
    }

    private static void assertCode(org.assertj.core.api.ThrowableAssert.ThrowingCallable call, String code) {
        assertThatThrownBy(call).isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo(code));
    }

    @Test
    void deletingAProjectRevokesAccessStopsWorkAndRemovesEveryByte() throws Exception {
        String subject = "delete-" + UUID.randomUUID();
        UUID project = createProject(subject, "My private dance");
        UUID owner = userId(subject);
        UUID clip = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");
        String original = put("assets/" + owner + "/" + project + "/" + clip + "/original");
        jdbc.sql("UPDATE assets SET object_key = :key, display_name = 'my-family-party.mp4' WHERE id = :id").param("key", original).param("id", clip).update();
        UUID select = selection(project, owner, clip);
        profile(project, owner, fixtures.readyAsset(owner, project, "TARGET_REFERENCE"), null);
        Ran ran = ran(owner, project, JobKind.PREVIEW, Map.of("source_asset_id", clip.toString(), "prompt", "secret"));

        mvc.perform(delete("/api/v1/projects/" + project).with(login(subject)).with(csrf())).andExpect(status().isNoContent());

        mvc.perform(get("/api/v1/projects/" + project).with(login(subject))).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/assets/" + clip).with(login(subject))).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/assets/" + clip + "/download-url").with(login(subject))).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/jobs/" + ran.job().id()).with(login(subject))).andExpect(status().isNotFound());

        // The worker finds out at its next call, whichever call that is, and nothing it says is accepted.
        assertCode(() -> leases.heartbeat(ran.job().id(), ran.attempt().attemptId(), ran.attempt().fencingToken()), "LEASE_LOST");
        assertCode(() -> leases.fail(ran.job().id(), new WorkerDtos.FailRequest(ran.attempt().attemptId(), ran.attempt().fencingToken(), "X", "x", false)), "LEASE_LOST");

        // Marks, prompts and names don't stay behind in the database.
        assertThat(jobs.find(ran.job().id())).isEmpty();
        assertThat(count("subject_selections", "id", select)).isZero();
        assertThat(count("appearance_profiles", "project_id", project)).isZero();
        assertThat(jdbc.sql("SELECT name FROM projects WHERE id = :id").param("id", project).query(String.class).single()).isEqualTo("Deleted project");
        assertThat(jdbc.sql("SELECT display_name FROM assets WHERE id = :id").param("id", clip).query(String.class).single()).isEqualTo("deleted");

        purger().purgeTombstones(Duration.ZERO);
        assertThat(storage.head(original)).isEmpty();
        assertThat(storage.head(ran.reported())).isEmpty();
        assertThat(storage.head(ran.unreported())).isEmpty();
    }

    @Test
    void deletingAClipRemovesItsMasksAndResultsButNotAnotherClips() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID clip = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");
        UUID other = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");
        UUID select = selection(project, owner, clip);
        Ran preprocess = ran(owner, project, JobKind.PREPROCESS, Map.of("source_asset_id", clip.toString()));
        jdbc.sql("""
                INSERT INTO preprocessing_artifacts (id, project_id, job_id, cache_key, source_asset_id, selection_id, config, tool_revision)
                VALUES (:id, :project, :job, :key, :source, :selection, '{}', 't')
                """).param("id", UUID.randomUUID()).param("project", project).param("job", preprocess.job().id())
                .param("key", "key-" + UUID.randomUUID()).param("source", clip).param("selection", select).update();
        Ran result = ran(owner, project, JobKind.PREVIEW, Map.of("source_asset_id", clip.toString()));
        Ran unrelated = ran(owner, project, JobKind.PREVIEW, Map.of("source_asset_id", other.toString()));

        deletion.deleteAsset(user(owner), clip);

        assertThat(jobs.find(preprocess.job().id())).isEmpty();
        assertThat(jobs.find(result.job().id())).isEmpty();
        assertThat(count("preprocessing_artifacts", "source_asset_id", clip)).isZero();
        assertThat(count("subject_selections", "id", select)).isZero();
        assertThat(jobs.find(unrelated.job().id())).isPresent();

        purger().purgeTombstones(Duration.ZERO);
        assertThat(storage.head(preprocess.reported())).isEmpty();
        assertThat(storage.head(result.unreported())).isEmpty();
        assertThat(storage.head(unrelated.reported())).isPresent();
    }

    @Test
    void deletingYourPhotoRemovesVideosMadeFromItAndKeepsTheMasks() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID clip = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");
        UUID photo = fixtures.readyAsset(owner, project, "TARGET_REFERENCE");
        UUID kept = profile(project, owner, photo, null);
        Ran preprocess = ran(owner, project, JobKind.PREPROCESS, Map.of("source_asset_id", clip.toString()));
        Ran result = ran(owner, project, JobKind.PREVIEW, Map.of("source_asset_id", clip.toString(), "primary_reference_asset_id", photo.toString()));

        deletion.deleteAsset(user(owner), photo);

        assertThat(jobs.find(result.job().id())).isEmpty();
        assertThat(jobs.find(preprocess.job().id())).isPresent();
        assertThat(count("appearance_profiles", "id", kept)).as("a profile can't outlive its primary photo").isZero();
        purger().purgeTombstones(Duration.ZERO);
        assertThat(storage.head(result.reported())).isEmpty();
        assertThat(storage.head(preprocess.reported())).isPresent();
    }

    @Test
    void deletingAPhotoThatWasOnlyStoredKeepsTheResults() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID clip = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");
        UUID primary = fixtures.readyAsset(owner, project, "TARGET_REFERENCE");
        UUID extra = fixtures.readyAsset(owner, project, "TARGET_REFERENCE");
        UUID kept = profile(project, owner, primary, extra);
        Ran result = ran(owner, project, JobKind.PREVIEW, Map.of("source_asset_id", clip.toString(),
                "primary_reference_asset_id", primary.toString(), "supplementary_reference_asset_ids", List.of(extra.toString())));

        deletion.deleteAsset(user(owner), extra);

        assertThat(jobs.find(result.job().id())).as("that photo was never fed to the model").isPresent();
        assertThat(count("appearance_profiles", "id", kept)).isOne();
        assertThat(count("reference_images", "asset_id", extra)).isZero();
    }

    @Test
    void aValidationJobsNormalizedCopyGoesWithItsPhoto() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID photo = fixtures.readyAsset(owner, project, "TARGET_REFERENCE");
        Ran validation = ran(owner, project, JobKind.VALIDATE, Map.of("asset_id", photo.toString()));
        deletion.deleteAsset(user(owner), photo);
        purger().purgeTombstones(Duration.ZERO);
        assertThat(storage.head(validation.reported())).isEmpty();
    }

    @Test
    void aRunningJobUsingADeletedPhotoIsStopped() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID photo = fixtures.readyAsset(owner, project, "TARGET_REFERENCE");
        Ran running = ran(owner, project, JobKind.PREVIEW, Map.of("primary_reference_asset_id", photo.toString()));
        deletion.deleteAsset(user(owner), photo);
        assertCode(() -> leases.heartbeat(running.job().id(), running.attempt().attemptId(), running.attempt().fencingToken()), "LEASE_LOST");
    }

    @Test
    void aMessageStillInTheQueueForADeletedJobIsDiscarded() {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        deletion.deleteProject(user(owner), project);
        assertThat(leases.acquire(job.id(), UUID.randomUUID(), 1, "w", Set.of(job.backendId()))).isInstanceOf(LeaseService.Refused.class);
    }

    @Test
    void oneResultCanBeDeletedWithoutTouchingOthers() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        Ran doomed = ran(owner, project, JobKind.PREVIEW, Map.of());
        Ran kept = ran(owner, project, JobKind.PREVIEW, Map.of());

        deletion.deleteResult(user(owner), doomed.job().id());

        assertThat(jobs.find(doomed.job().id())).isEmpty();
        assertThat(jobs.find(kept.job().id())).isPresent();
        purger().purgeTombstones(Duration.ZERO);
        assertThat(storage.head(doomed.reported())).isEmpty();
        assertThat(storage.head(kept.reported())).isPresent();
    }

    @Test
    void masksCantBeDeletedAsAResult() {
        UUID owner = fixtures.user();
        JobRow preprocess = fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREPROCESS);
        assertCode(() -> deletion.deleteResult(user(owner), preprocess.id()), "NOT_A_RESULT");
    }

    @Test
    void aTombstoneStaysOpenWhileASignedUploadUrlCouldStillWrite() throws Exception {
        UUID owner = fixtures.user();
        UUID prefixId = UUID.randomUUID();
        jdbc.sql("INSERT INTO deletion_tombstones (object_key, owner_id) VALUES (:key, :owner)")
                .param("key", "attempts/" + prefixId + "/").param("owner", owner).update();

        purger().purgeTombstones(Duration.ofHours(1));
        assertThat(jdbc.sql("SELECT count(*) FROM deletion_tombstones WHERE object_key = :key AND purged_at IS NULL")
                .param("key", "attempts/" + prefixId + "/").query(Long.class).single()).isOne();

        // A late upload after the first pass is caught by the next one.
        String late = put("attempts/" + prefixId + "/late.mp4");
        purger().purgeTombstones(Duration.ZERO);
        assertThat(storage.head(late)).isEmpty();
    }

    @Test
    void oneObjectThatWontDeleteDoesNotHoldBackTheOthers() {
        UUID owner = fixtures.user();
        String failing = "aaa-failing-" + UUID.randomUUID();
        String healthy = "zzz-healthy-" + UUID.randomUUID();
        for (String key : List.of(failing, healthy)) {
            jdbc.sql("INSERT INTO deletion_tombstones (object_key, owner_id) VALUES (:key, :owner)").param("key", key).param("owner", owner).update();
        }
        ObjectStorage broken = mock(ObjectStorage.class);
        doThrow(new IllegalStateException("storage down")).when(broken).delete(eq(failing));

        purger(broken).purgeTombstones(Duration.ZERO);

        verify(broken).delete(healthy);
        assertThat(jdbc.sql("SELECT purged_at IS NULL FROM deletion_tombstones WHERE object_key = :key").param("key", failing).query(Boolean.class).single())
                .as("still open, so it is retried").isTrue();
        assertThat(jdbc.sql("SELECT purged_at IS NULL FROM deletion_tombstones WHERE object_key = :key").param("key", healthy).query(Boolean.class).single()).isFalse();
    }

    @Test
    void anotherUserCantDeleteYourData() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID asset = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        var mallory = login("mallory-" + UUID.randomUUID());
        mvc.perform(delete("/api/v1/projects/" + project).with(mallory).with(csrf())).andExpect(status().isNotFound());
        mvc.perform(delete("/api/v1/assets/" + asset).with(mallory).with(csrf())).andExpect(status().isNotFound());
        mvc.perform(delete("/api/v1/jobs/" + job.id()).with(mallory).with(csrf())).andExpect(status().isNotFound());
        assertThat(jdbc.sql("SELECT status FROM projects WHERE id = :id").param("id", project).query(String.class).single()).isEqualTo("ACTIVE");
        assertThat(jobs.find(job.id())).isPresent();
    }

    @Test
    void deletingNeedsACsrfToken() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        mvc.perform(delete("/api/v1/projects/" + project).with(login("x"))).andExpect(status().isForbidden());
    }

    @Test
    void deletionIsAuditedWithoutPersonalDetails() {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        deletion.deleteProject(user(owner), project);
        assertThat(jdbc.sql("SELECT action FROM audit_events WHERE target_id = :id").param("id", project.toString()).query(String.class).list())
                .contains("PROJECT_DELETED");
    }

    @Test
    void anAlreadyDeletedProjectIsNotFound() {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        deletion.deleteProject(user(owner), project);
        assertCode(() -> deletion.deleteProject(user(owner), project), "NOT_FOUND");
    }

    @Test
    void abandonedUploadsAreCleanedUp() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        UUID asset = UUID.randomUUID();
        String key = put("uploads/" + owner + "/" + project + "/" + asset + "/upload");
        jdbc.sql("""
                INSERT INTO assets (id, project_id, owner_id, role, status, display_name, declared_type, declared_bytes, upload_key, upload_expires_at)
                VALUES (:id, :project, :owner, 'DRIVING_VIDEO', 'PENDING_UPLOAD', 'x', 'video/mp4', 1, :key, now() - interval '2 hours')
                """).param("id", asset).param("project", project).param("owner", owner).param("key", key).update();

        purger().expireAbandonedUploads();

        assertThat(storage.head(key)).isEmpty();
        assertThat(jdbc.sql("SELECT status FROM assets WHERE id = :id").param("id", asset).query(String.class).single()).isEqualTo("DELETED");
    }

    private void age(String table, String column, UUID id) {
        jdbc.sql("UPDATE " + table + " SET " + column + " = now() - interval '40 days' WHERE id = :id").param("id", id).update();
    }

    private String projectStatus(UUID id) {
        return jdbc.sql("SELECT status FROM projects WHERE id = :id").param("id", id).query(String.class).single();
    }

    @Test
    void retentionRemovesIdleProjectsAndLeavesActiveOnes() throws Exception {
        UUID owner = fixtures.user();
        UUID idle = fixtures.project(owner);
        age("projects", "updated_at", idle);
        UUID usedRecently = fixtures.project(owner);
        age("projects", "updated_at", usedRecently);
        fixtures.readyAsset(owner, usedRecently, "DRIVING_VIDEO");
        UUID running = fixtures.project(owner);
        age("projects", "updated_at", running);
        JobRow stillRunning = fixtures.queuedJob(owner, running, JobKind.PREVIEW);
        jdbc.sql("UPDATE generation_jobs SET created_at = now() - interval '40 days' WHERE id = :id").param("id", stillRunning.id()).update();
        jdbc.sql("UPDATE assets SET created_at = now() - interval '40 days' WHERE project_id = :id").param("id", running).update();

        purger().expireIdleProjects(30);

        assertThat(projectStatus(idle)).isEqualTo("DELETED");
        assertThat(projectStatus(usedRecently)).isEqualTo("ACTIVE");
        assertThat(projectStatus(running)).isEqualTo("ACTIVE");
        assertThat(jdbc.sql("SELECT action FROM audit_events WHERE target_id = :id").param("id", idle.toString()).query(String.class).list()).contains("PROJECT_EXPIRED");
    }

    @Test
    void aSelectionOverTheLimitNeedsATrimAndCantCrossACut() throws Exception {
        String subject = "trim-" + UUID.randomUUID();
        UUID project = createProject(subject, "Long");
        UUID asset = fixtures.readyAsset(userId(subject), project, "DRIVING_VIDEO");
        jdbc.sql("UPDATE assets SET media = CAST(:media AS jsonb) WHERE id = :id")
                .param("media", "{\"duration_seconds\": 60, \"cuts\": [12.5]}").param("id", asset).update();

        Map<String, Object> base = Map.of("sourceAssetId", asset, "frameIndex", 0, "timestampNum", 0, "timestampDen", 30,
                "coordinateMapping", Map.of(), "annotations", Map.of("points", List.of(Map.of("x", 0.5, "y", 0.5, "positive", true, "frame", 0))));
        mvc.perform(post("/api/v1/projects/" + project + "/subject-selections").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(Json.write(base)))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("TRIM_REQUIRED"));

        Map<String, Object> acrossCut = new HashMap<>(base);
        acrossCut.put("trimStartSeconds", 10.0);
        acrossCut.put("trimEndSeconds", 20.0);
        mvc.perform(post("/api/v1/projects/" + project + "/subject-selections").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(Json.write(acrossCut)))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("MULTIPLE_SHOTS"));

        Map<String, Object> oneShot = new HashMap<>(base);
        oneShot.put("trimStartSeconds", 13.0);
        oneShot.put("trimEndSeconds", 20.0);
        mvc.perform(post("/api/v1/projects/" + project + "/subject-selections").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(Json.write(oneShot)))
                .andExpect(status().isCreated());
    }
}

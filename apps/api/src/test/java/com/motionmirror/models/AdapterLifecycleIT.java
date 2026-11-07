package com.motionmirror.models;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.oidcLogin;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.config.SecurityConfig;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.presets.PresetService;
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
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;

@IntegrationTest
class AdapterLifecycleIT {

    private static final String BASE = "Wan-AI/Wan2.2-Animate-14B-Diffusers";
    private static final String REVISION = "6f4df10861c758af86ac3c979aacc1bf5c03eff0";
    private static final JsonNode PASSING_EVAL = Json.tree(Map.of(
            "hardware", Map.of("device_type", "cuda"), "human_review", Map.of("passed", true)));

    @Autowired AdapterService adapters;
    @Autowired PresetService presets;
    @Autowired ObjectStorage storage;
    @Autowired Fixtures fixtures;
    @Autowired JdbcClient jdbc;
    @Autowired MockMvc mvc;
    @Autowired com.motionmirror.jobs.JobService jobService;
    @Autowired LeaseService leases;

    CurrentUser admin;
    String presetId;

    @BeforeEach
    void setUp() {
        admin = new CurrentUser(fixtures.user(), Set.of("ROLE_ADMIN", "ROLE_USER"));
        presetId = "test-" + UUID.randomUUID().toString().substring(0, 8);
        jdbc.sql("""
                INSERT INTO presets (id, revision, display_name, backend_id, definition)
                VALUES (:id, 1, 'Test', 'configured', CAST(:definition AS jsonb))
                """).param("id", presetId).param("definition", "{\"id\":\"x\",\"revision\":1,\"settings\":{}}").update();
        jdbc.sql("INSERT INTO preset_heads (id, current_revision) VALUES (:id, 1)").param("id", presetId).update();
    }

    private String uploadArtifact(UUID id, byte[] body) throws Exception {
        String url = storage.presignAdminWrite("adapters/" + id + "/adapter.safetensors");
        HttpResponse<String> response = HttpClient.newHttpClient().send(HttpRequest.newBuilder(URI.create(url))
                .header("Content-Type", "application/octet-stream").PUT(HttpRequest.BodyPublishers.ofByteArray(body)).build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(response.statusCode()).isEqualTo(200);
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body));
    }

    private AdapterService.NewAdapter request(UUID id, String sha, String revision, String scope, JsonNode eval) {
        return new AdapterService.NewAdapter(id, "Test adapter", BASE, revision, sha, List.of("q", "k", "v"), 32, 1.0,
                "CC BY-NC 4.0", scope, Json.tree(List.of("tiktok-dance@kaggle-2021-09-22")),
                Json.tree(Map.of("device", "cuda", "torch", "2.15.0", "dtype", "bfloat16")), eval);
    }

    private AdapterService.Adapter registered(String scope, JsonNode eval) throws Exception {
        UUID id = UUID.randomUUID();
        return adapters.register(admin, request(id, uploadArtifact(id, ("weights-" + id).getBytes()), REVISION, scope, eval));
    }

    private int head() {
        return jdbc.sql("SELECT current_revision FROM preset_heads WHERE id = :id").param("id", presetId).query(Integer.class).single();
    }

    private static void assertCode(org.assertj.core.api.ThrowableAssert.ThrowingCallable call, String code) {
        assertThatThrownBy(call).isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo(code));
    }

    @Test
    void aBaseRevisionMismatchIsRefused() throws Exception {
        UUID id = UUID.randomUUID();
        String sha = uploadArtifact(id, new byte[] {1});
        assertCode(() -> adapters.register(admin, request(id, sha, "0".repeat(40), "production", null)), "BASE_REVISION_MISMATCH");
    }

    @Test
    void aHashMismatchIsRefused() throws Exception {
        UUID id = UUID.randomUUID();
        uploadArtifact(id, new byte[] {1});
        assertCode(() -> adapters.register(admin, request(id, "a".repeat(64), REVISION, "production", null)), "HASH_MISMATCH");
    }

    @Test
    void aMissingArtifactIsRefused() {
        assertCode(() -> adapters.register(admin, request(UUID.randomUUID(), "a".repeat(64), REVISION, "production", null)), "ARTIFACT_NOT_UPLOADED");
    }

    @Test
    void stagingWithoutAnEvaluationIsRefused() throws Exception {
        AdapterService.Adapter adapter = registered("production", null);
        assertThat(adapter.state()).isEqualTo("REGISTERED");
        assertCode(() -> adapters.stage(admin, adapter.id(), presetId), "EVALUATION_MISSING");
    }

    @Test
    void aMacEvaluationCannotCount() throws Exception {
        AdapterService.Adapter adapter = registered("production", null);
        JsonNode mac = Json.tree(Map.of("hardware", Map.of("device_type", "mps"), "human_review", Map.of("passed", true)));
        assertCode(() -> adapters.attachEvaluation(admin, adapter.id(), mac), "EVALUATION_NOT_PRODUCTION_CLASS");
    }

    @Test
    void anEvaluationWithoutAHumanPassCannotCount() throws Exception {
        AdapterService.Adapter adapter = registered("production", null);
        JsonNode unreviewed = Json.tree(Map.of("hardware", Map.of("device_type", "cuda"), "human_review", Map.of("passed", false)));
        assertCode(() -> adapters.attachEvaluation(admin, adapter.id(), unreviewed), "EVALUATION_NOT_PASSED");
    }

    @Test
    void aResearchAdapterCanBeStagedButNeverPromoted() throws Exception {
        AdapterService.Adapter adapter = registered("research_only", PASSING_EVAL);
        int staged = adapters.stage(admin, adapter.id(), presetId);
        assertThat(head()).isEqualTo(1);
        assertThat(staged).isEqualTo(2);
        assertCode(() -> adapters.promote(admin, adapter.id(), presetId), "RESEARCH_ADAPTER");
        assertThat(head()).isEqualTo(1);
    }

    @Test
    void stagingDoesNotChangeTheDefaultAndIsHiddenFromOrdinaryUsers() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        int staged = adapters.stage(admin, adapter.id(), presetId);
        assertThat(head()).isEqualTo(1);
        assertThat(presets.resolveReference(presetId + "@" + staged, true).definition().path("adapter").path("sha256").asText()).isEqualTo(adapter.sha256());
        assertCode(() -> presets.resolveReference(presetId + "@" + staged, false), "UNKNOWN_PRESET");
    }

    @Test
    void promotingNeedsStagingFirst() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        assertCode(() -> adapters.promote(admin, adapter.id(), presetId), "ADAPTER_NOT_STAGED");
    }

    @Test
    void promotionMovesTheDefaultAndRollbackReturnsItWithAudit() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.stage(admin, adapter.id(), presetId);
        int promoted = adapters.promote(admin, adapter.id(), presetId);

        assertThat(head()).isEqualTo(promoted);
        assertThat(presets.requireCurrent(presetId, false).definition().path("adapter").path("id").asText()).isEqualTo(adapter.id().toString());

        assertThat(adapters.rollback(admin, presetId)).isEqualTo(1);
        assertThat(head()).isEqualTo(1);
        assertThat(presets.requireCurrent(presetId, false).definition().hasNonNull("adapter")).isFalse();

        List<String> actions = jdbc.sql("SELECT action FROM audit_events WHERE target_id IN (:adapter, :preset)")
                .param("adapter", adapter.id().toString()).param("preset", presetId).query(String.class).list();
        assertThat(actions).contains("ADAPTER_REGISTERED", "ADAPTER_STAGED", "ADAPTER_PROMOTED", "PRESET_ROLLED_BACK");
    }

    @Test
    void revisionsAreImmutableSoARunningJobsSnapshotIsUntouched() throws Exception {
        String before = jdbc.sql("SELECT definition::text FROM presets WHERE id = :id AND revision = 1").param("id", presetId).query(String.class).single();
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.stage(admin, adapter.id(), presetId);
        adapters.promote(admin, adapter.id(), presetId);
        adapters.rollback(admin, presetId);
        assertThat(jdbc.sql("SELECT definition::text FROM presets WHERE id = :id AND revision = 1").param("id", presetId).query(String.class).single()).isEqualTo(before);
    }

    @Test
    void rollingBackWithNothingEarlierIsRefused() {
        assertCode(() -> adapters.rollback(admin, presetId), "NOTHING_TO_ROLL_BACK");
    }

    @Test
    void aDeprecatedAdapterCantBeStaged() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.deprecate(admin, adapter.id());
        assertCode(() -> adapters.stage(admin, adapter.id(), presetId), "ADAPTER_WRONG_STATE");
    }

    @Test
    void anOrdinaryUserCantUseAdminEndpoints() throws Exception {
        var user = oidcLogin().idToken(token -> token.subject("plain-user").claim("iss", "https://issuer.test"));
        mvc.perform(get("/api/v1/admin/adapters").with(user)).andExpect(status().isForbidden());
        mvc.perform(post("/api/v1/admin/adapters/upload-target").with(jwt().authorities(new SimpleGrantedAuthority("ROLE_USER"))))
                .andExpect(status().isForbidden());
    }

    @Test
    void aRegistrationNamingAPathOrUrlHasNowhereToPutIt() throws Exception {
        var admin = jwt().jwt(token -> token.claim("roles", List.of("ADMIN")).claim("iss", "https://issuer.test"))
                .authorities(new SimpleGrantedAuthority("ROLE_ADMIN"));
        Map<String, Object> request = new java.util.HashMap<>(Map.of("id", UUID.randomUUID(), "name", "x", "baseModelId", BASE, "baseRevision", REVISION,
                "sha256", "a".repeat(64), "targetModules", List.of(), "rank", 1, "scale", 1.0, "license", "x"));
        request.putAll(Map.of("licenseScope", "production", "datasetVersions", List.of(), "trainedOn", Map.of(), "artifactKey", "../../etc/passwd"));
        String body = Json.write(request);
        // The API derives the key itself, so the supplied one is ignored and nothing was uploaded there.
        mvc.perform(post("/api/v1/admin/adapters").with(admin).contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isConflict());
    }

    @Test
    void anAdapterThatIsADefaultCantBeDeprecatedUntilRolledBack() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.stage(admin, adapter.id(), presetId);
        adapters.promote(admin, adapter.id(), presetId);
        assertCode(() -> adapters.deprecate(admin, adapter.id()), "ADAPTER_IN_USE");
        adapters.rollback(admin, presetId);
        assertThat(adapters.deprecate(admin, adapter.id()).state()).isEqualTo("DEPRECATED");
    }

    @Test
    void afterARollbackTheAdapterCanBePromotedAgain() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.stage(admin, adapter.id(), presetId);
        adapters.promote(admin, adapter.id(), presetId);
        adapters.rollback(admin, presetId);
        assertThat(adapters.require(adapter.id()).state()).isEqualTo("STAGED");
        assertThat(adapters.promote(admin, adapter.id(), presetId)).isEqualTo(head());
    }

    @Test
    void stagingAPromotedAdapterForAnotherPresetDoesNotDemoteIt() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.stage(admin, adapter.id(), presetId);
        adapters.promote(admin, adapter.id(), presetId);
        adapters.stage(admin, adapter.id(), presetId);
        assertThat(adapters.require(adapter.id()).state()).isEqualTo("PROMOTED");
    }

    @Test
    void theSnapshotCarriesTheLicenceScopeSoManifestsCanLabelIt() throws Exception {
        AdapterService.Adapter adapter = registered("research_only", PASSING_EVAL);
        int staged = adapters.stage(admin, adapter.id(), presetId);
        var snapshot = presets.resolveReference(presetId + "@" + staged, true).definition().path("adapter");
        assertThat(snapshot.path("license_scope").asText()).isEqualTo("research_only");
    }

    private com.motionmirror.jobs.JobRow jobNaming(AdapterService.Adapter adapter) {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        return jobService.submit(owner, new com.motionmirror.jobs.JobService.Submission(project, com.motionmirror.jobs.JobKind.PREVIEW,
                presetId + "@2", "{}", "{\"seed\":1}", Json.write(Map.of("adapter", Map.of("id", adapter.id().toString()))), null, true));
    }

    @Test
    void aWorkerGetsOnlyTheAdapterItsOwnJobNames() throws Exception {
        AdapterService.Adapter named = registered("production", PASSING_EVAL);
        AdapterService.Adapter other = registered("production", PASSING_EVAL);
        com.motionmirror.jobs.JobRow job = jobNaming(named);
        var attempt = ((LeaseService.Acquired) leases.acquire(job.id(), UUID.randomUUID(), 1, "w", Set.of(job.backendId()))).response();
        var fence = new WorkerDtos.AdapterAccessRequest(attempt.attemptId(), attempt.fencingToken());

        var access = leases.adapterAccess(named.id(), fence);
        assertThat(access.sha256()).isEqualTo(named.sha256());
        assertThat(access.url()).contains("adapters/" + named.id());

        assertCode(() -> leases.adapterAccess(other.id(), fence), "ADAPTER_NOT_IN_JOB");
        assertCode(() -> leases.adapterAccess(named.id(), new WorkerDtos.AdapterAccessRequest(attempt.attemptId(), attempt.fencingToken() + 1)), "LEASE_LOST");
    }

    private com.fasterxml.jackson.databind.JsonNode seedFile(String id, int revision, int seed) {
        return new com.fasterxml.jackson.databind.ObjectMapper()
                .valueToTree(Map.of("id", id, "revision", revision, "display_name", "Seeded", "settings", Map.of("seed", seed)));
    }

    @Test
    void changingASeededRevisionStopsStartupInsteadOfBeingIgnored() {
        String id = "seed-" + UUID.randomUUID();
        presets.seedDefinition(seedFile(id, 1, 1));
        presets.seedDefinition(seedFile(id, 1, 1));
        assertThatThrownBy(() -> presets.seedDefinition(seedFile(id, 1, 2)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("new revision");
    }

    @Test
    void aNewRevisionFromAFileBecomesTheDefault() {
        String id = "seed-" + UUID.randomUUID();
        presets.seedDefinition(seedFile(id, 1, 1));
        presets.seedDefinition(seedFile(id, 2, 2));
        assertThat(jdbc.sql("SELECT current_revision FROM preset_heads WHERE id = :id").param("id", id).query(Integer.class).single()).isEqualTo(2);
    }

    @Test
    void aDeployCantSilentlyDropAPromotedAdapter() throws Exception {
        AdapterService.Adapter adapter = registered("production", PASSING_EVAL);
        adapters.stage(admin, adapter.id(), presetId);
        int promoted = adapters.promote(admin, adapter.id(), presetId);
        presets.seedDefinition(seedFile(presetId, promoted + 1, 9));
        assertThat(head()).isEqualTo(promoted);
    }

    @Test
    void operatorRolesComeOnlyFromTheVerifiedClaim() {
        var token = org.springframework.security.oauth2.jwt.Jwt.withTokenValue("t").header("alg", "RS256").subject("s")
                .claim("roles", List.of("ADMIN", "SUPERUSER")).build();
        assertThat(SecurityConfig.operatorAuthentication(token).getAuthorities()).extracting(Object::toString).containsExactly("ROLE_ADMIN");
    }
}

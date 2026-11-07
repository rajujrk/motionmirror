package com.motionmirror.assets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.oidcLogin;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.Json;
import com.motionmirror.support.IntegrationTest;
import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.OidcLoginRequestPostProcessor;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;

/** Upload abuse against a real S3-compatible service: the storage layer itself must enforce the limits. */
@IntegrationTest
class UploadIT {

    @Autowired MockMvc mvc;
    @Autowired ObjectStorage storage;
    @Autowired JdbcClient jdbc;
    @Autowired AssetService assets;

    String projectId;

    private static OidcLoginRequestPostProcessor login(String subject) {
        return oidcLogin().idToken(token -> token.subject(subject).claim("iss", "https://issuer.test"));
    }

    private JsonNode postJson(String path, String subject, String body, int expected) throws Exception {
        String response = mvc.perform(post(path).with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().is(expected)).andReturn().getResponse().getContentAsString();
        return Json.read(response);
    }

    @BeforeEach
    void setUp() throws Exception {
        projectId = postJson("/api/v1/projects", "alice", "{\"name\":\"Dance\"}", 201).path("id").asString();
    }

    private JsonNode intent(long size) throws Exception {
        return postJson("/api/v1/projects/" + projectId + "/assets/upload-intents", "alice", Json.write(Map.of(
                "role", "DRIVING_VIDEO", "filename", "../../etc/passwd<script>.mp4", "sizeBytes", size,
                "contentType", "video/mp4", "consentConfirmed", true)), 201);
    }

    /** A real browser-style multipart POST to the storage service. */
    private int upload(JsonNode intent, byte[] content, String contentTypeField) throws Exception {
        String boundary = "----mm" + UUID.randomUUID();
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        intent.path("fields").properties().forEach(field -> {
            String value = field.getKey().equals("Content-Type") && contentTypeField != null ? contentTypeField : field.getValue().asString();
            write(body, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + field.getKey() + "\"\r\n\r\n" + value + "\r\n");
        });
        write(body, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f\"\r\nContent-Type: video/mp4\r\n\r\n");
        body.write(content);
        write(body, "\r\n--" + boundary + "--\r\n");
        return HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(URI.create(intent.path("url").asString()))
                        .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                        .POST(HttpRequest.BodyPublishers.ofByteArray(body.toByteArray())).build(),
                HttpResponse.BodyHandlers.ofString()).statusCode();
    }

    private static void write(ByteArrayOutputStream out, String text) {
        out.writeBytes(text.getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void aMatchingUploadSucceedsAndMovesToAServerOnlyKey() throws Exception {
        byte[] content = new byte[1024];
        JsonNode intent = intent(content.length);
        assertThat(upload(intent, content, null)).isBetween(200, 204);

        String assetId = intent.path("assetId").asString();
        JsonNode asset = postJson("/api/v1/assets/" + assetId + "/complete", "alice", "", 200);
        assertThat(asset.path("status").asString()).isEqualTo("QUARANTINED");
        assertThat(asset.path("displayName").asString()).doesNotContain("/", "<");

        String objectKey = jdbc.sql("SELECT object_key FROM assets WHERE id = :id").param("id", UUID.fromString(assetId)).query(String.class).single();
        assertThat(objectKey).startsWith("assets/");
        assertThat(storage.head(objectKey)).isPresent();
        String uploadKey = jdbc.sql("SELECT upload_key FROM assets WHERE id = :id").param("id", UUID.fromString(assetId)).query(String.class).single();
        assertThat(storage.head(uploadKey)).isEmpty();
    }

    @Test
    void theStorageServiceRejectsAFileLargerThanThePolicyAllows() throws Exception {
        // The role limit is hundreds of MB, so prove the service enforces the policy with a small signed limit.
        ObjectStorage.UploadForm tiny = storage.presignUploadForm("uploads/test/" + UUID.randomUUID(), "video/mp4", 100);
        JsonNode tinyIntent = Json.tree(Map.of("url", tiny.url(), "fields", tiny.fields()));
        assertThat(upload(tinyIntent, new byte[101], null)).isGreaterThanOrEqualTo(400);
        assertThat(upload(tinyIntent, new byte[100], null)).isBetween(200, 204);
    }

    @Test
    void theStorageServiceRejectsAChangedContentType() throws Exception {
        assertThat(upload(intent(1024), new byte[1024], "text/html")).isGreaterThanOrEqualTo(400);
    }

    @Test
    void aStillValidUploadFormCannotReplaceAFinalizedAsset() throws Exception {
        byte[] original = new byte[1024];
        JsonNode intent = intent(original.length);
        assertThat(upload(intent, original, null)).isBetween(200, 204);
        String assetId = intent.path("assetId").asString();
        postJson("/api/v1/assets/" + assetId + "/complete", "alice", "", 200);
        String objectKey = jdbc.sql("SELECT object_key FROM assets WHERE id = :id").param("id", UUID.fromString(assetId)).query(String.class).single();
        String etag = storage.head(objectKey).orElseThrow().etag();

        byte[] replacement = new byte[1024];
        replacement[0] = 42;
        upload(intent, replacement, null);

        assertThat(storage.head(objectKey).orElseThrow().etag()).isEqualTo(etag);
        // Completing again does not pull the new bytes in either.
        postJson("/api/v1/assets/" + assetId + "/complete", "alice", "", 200);
        assertThat(storage.head(objectKey).orElseThrow().etag()).isEqualTo(etag);
    }

    @Test
    void aCompletionThatFailedAfterCopyingTheFileCanBeCompletedAgain() throws Exception {
        byte[] content = new byte[1024];
        JsonNode intent = intent(content.length);
        assertThat(upload(intent, content, null)).isBetween(200, 204);
        UUID assetId = UUID.fromString(intent.path("assetId").asString());
        Map<String, Object> row = jdbc.sql("SELECT owner_id, upload_key FROM assets WHERE id = :id").param("id", assetId)
                .query().singleRow();
        // What an earlier call leaves behind when its transaction rolls back: storage moved on, the database didn't.
        String immutableKey = "assets/" + row.get("owner_id") + "/" + projectId + "/" + assetId + "/original";
        storage.copy((String) row.get("upload_key"), immutableKey);
        storage.delete((String) row.get("upload_key"));

        JsonNode asset = postJson("/api/v1/assets/" + assetId + "/complete", "alice", "", 200);

        assertThat(asset.path("status").asString()).isEqualTo("QUARANTINED");
        assertThat(validationJobs(assetId)).isOne();
    }

    @Test
    void twoCompletionsAtOnceBothSucceedAndStartOneCheck() throws Exception {
        byte[] content = new byte[1024];
        JsonNode intent = intent(content.length);
        assertThat(upload(intent, content, null)).isBetween(200, 204);
        UUID assetId = UUID.fromString(intent.path("assetId").asString());
        UUID owner = jdbc.sql("SELECT owner_id FROM assets WHERE id = :id").param("id", assetId).query(UUID.class).single();
        CurrentUser alice = new CurrentUser(owner, Set.of("ROLE_USER"));

        try (ExecutorService pool = Executors.newFixedThreadPool(2)) {
            List<Future<AssetEntity>> results = pool.invokeAll(List.<Callable<AssetEntity>>of(
                    () -> assets.complete(alice, assetId), () -> assets.complete(alice, assetId)));
            for (Future<AssetEntity> result : results) {
                assertThat(result.get().getStatus()).isEqualTo(AssetEntity.QUARANTINED);
            }
        }
        assertThat(validationJobs(assetId)).isOne();
    }

    private long validationJobs(UUID assetId) {
        return jdbc.sql("SELECT count(*) FROM generation_jobs WHERE kind = 'VALIDATE' AND idempotency_key = :key")
                .param("key", "validate:" + assetId).query(Long.class).single();
    }

    @Test
    void completingWithoutAnUploadIsRefused() throws Exception {
        JsonNode intent = intent(1024);
        postJson("/api/v1/assets/" + intent.path("assetId").asString() + "/complete", "alice", "", 409);
    }

    @Test
    void aFileOfADifferentSizeThanDeclaredIsRejected() throws Exception {
        JsonNode intent = intent(1024);
        assertThat(upload(intent, new byte[2048], null)).isBetween(200, 204);
        JsonNode asset = postJson("/api/v1/assets/" + intent.path("assetId").asString() + "/complete", "alice", "", 200);
        assertThat(asset.path("status").asString()).isEqualTo("REJECTED");
        assertThat(asset.path("rejectionCode").asString()).isEqualTo("SIZE_MISMATCH");
    }

    @Test
    void executableAndHtmlTypesAreRefusedUpFront() throws Exception {
        for (String type : new String[] {"text/html", "image/svg+xml", "application/x-msdownload"}) {
            mvc.perform(post("/api/v1/projects/" + projectId + "/assets/upload-intents").with(login("alice")).with(csrf())
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(Json.write(Map.of("role", "TARGET_REFERENCE", "filename", "x", "sizeBytes", 10, "contentType", type, "consentConfirmed", true))))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("UNSUPPORTED_MEDIA_TYPE"));
        }
    }

    @Test
    void anOversizedDeclarationIsRefusedUpFront() throws Exception {
        mvc.perform(post("/api/v1/projects/" + projectId + "/assets/upload-intents").with(login("alice")).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("role", "DRIVING_VIDEO", "filename", "x.mp4", "sizeBytes", 99_000_000_000L, "contentType", "video/mp4", "consentConfirmed", true))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("FILE_TOO_LARGE"));
    }

    @Test
    void uploadsNeedConsent() throws Exception {
        mvc.perform(post("/api/v1/projects/" + projectId + "/assets/upload-intents").with(login("alice")).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("role", "DRIVING_VIDEO", "filename", "x.mp4", "sizeBytes", 10, "contentType", "video/mp4", "consentConfirmed", false))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("CONSENT_REQUIRED"));
    }

    @Test
    void aQuarantinedAssetCannotBeDownloaded() throws Exception {
        byte[] content = new byte[64];
        JsonNode intent = intent(content.length);
        upload(intent, content, null);
        String assetId = intent.path("assetId").asString();
        postJson("/api/v1/assets/" + assetId + "/complete", "alice", "", 200);
        mvc.perform(get("/api/v1/assets/" + assetId + "/download-url").with(login("alice")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ASSET_NOT_READY"));
    }
}

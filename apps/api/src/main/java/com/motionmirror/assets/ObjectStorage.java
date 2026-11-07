package com.motionmirror.assets;

import com.motionmirror.config.AppProperties;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.io.IOException;
import java.net.URI;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Service;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.BucketAlreadyOwnedByYouException;
import software.amazon.awssdk.services.s3.model.CORSRule;
import software.amazon.awssdk.services.s3.model.HeadObjectResponse;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.S3Exception;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

/** Private S3-compatible storage. Signed URLs are built for the network of whoever will use them. */
@Service
public class ObjectStorage {

    public record ObjectInfo(long sizeBytes, String contentType, String etag) {}

    public record UploadForm(String url, Map<String, String> fields, Instant expiresAt) {}

    private final AppProperties.Storage config;
    private final AppProperties properties;
    private final S3Client client;
    private final S3Presigner browserPresigner;
    private final S3Presigner workerPresigner;

    public ObjectStorage(AppProperties properties) {
        this.properties = properties;
        this.config = properties.storage();
        StaticCredentialsProvider credentials = StaticCredentialsProvider.create(
                AwsBasicCredentials.create(config.accessKey(), config.secretKey()));
        Region region = Region.of(config.region());
        this.client = S3Client.builder()
                .endpointOverride(URI.create(config.internalEndpoint()))
                .region(region)
                .credentialsProvider(credentials)
                .forcePathStyle(true)
                .build();
        S3Configuration pathStyle = S3Configuration.builder().pathStyleAccessEnabled(true).build();
        this.browserPresigner = S3Presigner.builder()
                .endpointOverride(URI.create(config.publicEndpoint()))
                .region(region).credentialsProvider(credentials).serviceConfiguration(pathStyle).build();
        this.workerPresigner = S3Presigner.builder()
                .endpointOverride(URI.create(config.workerEndpoint()))
                .region(region).credentialsProvider(credentials).serviceConfiguration(pathStyle).build();
    }

    @PostConstruct
    void prepareBucket() {
        ensureBucket();
    }

    public void ensureBucket() {
        try {
            client.createBucket(request -> request.bucket(config.bucket()));
        } catch (BucketAlreadyOwnedByYouException alreadyThere) {
            // Nothing to do.
        } catch (S3Exception error) {
            if (error.statusCode() != 409) {
                throw error;
            }
        }
        client.putBucketCors(request -> request.bucket(config.bucket()).corsConfiguration(cors -> cors.corsRules(
                CORSRule.builder()
                        .allowedOrigins(List.of(properties.publicOrigin()))
                        .allowedMethods("POST", "GET", "HEAD")
                        .allowedHeaders("*")
                        .exposeHeaders("ETag")
                        .maxAgeSeconds(3600)
                        .build())));
    }

    /** The storage service itself rejects a different key, a wrong type or anything over maxBytes. */
    public UploadForm presignUploadForm(String key, String contentType, long maxBytes) {
        Instant now = Instant.now();
        Instant expires = now.plus(config.uploadUrlTtl());
        Map<String, String> fields = PostPolicySigner.sign(config.bucket(), key, contentType, maxBytes, now, expires,
                config.accessKey(), config.secretKey(), config.region());
        return new UploadForm(config.publicEndpoint() + "/" + config.bucket(), fields, expires);
    }

    public String presignBrowserDownload(String key, String downloadName) {
        return presignGet(browserPresigner, key, config.downloadUrlTtl(), downloadName);
    }

    public String presignWorkerRead(String key) {
        return presignGet(workerPresigner, key, config.workerUrlTtl(), null);
    }

    public String presignWorkerWrite(String key, String contentType) {
        return workerPresigner.presignPutObject(request -> request
                        .signatureDuration(config.workerUrlTtl())
                        .putObjectRequest(put -> put.bucket(config.bucket()).key(key).contentType(contentType)))
                .url().toString();
    }

    /** Reachable from the operator's machine, for uploading a trained adapter. */
    public String presignAdminWrite(String key) {
        return browserPresigner.presignPutObject(request -> request
                        .signatureDuration(config.uploadUrlTtl())
                        .putObjectRequest(put -> put.bucket(config.bucket()).key(key).contentType("application/octet-stream")))
                .url().toString();
    }

    /** Hashed here rather than trusted from a request, so a registered hash always describes the stored bytes. */
    public String sha256(String key) {
        try (var stream = client.getObject(request -> request.bucket(config.bucket()).key(key))) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[1024 * 1024];
            for (int read = stream.read(buffer); read >= 0; read = stream.read(buffer)) {
                digest.update(buffer, 0, read);
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (IOException | NoSuchAlgorithmException error) {
            throw new IllegalStateException("cannot hash " + key, error);
        }
    }

    private String presignGet(S3Presigner presigner, String key, Duration ttl, String downloadName) {
        return presigner.presignGetObject(request -> request
                        .signatureDuration(ttl)
                        .getObjectRequest(get -> {
                            get.bucket(config.bucket()).key(key);
                            if (downloadName != null) {
                                get.responseContentDisposition("attachment; filename=\"" + downloadName + "\"");
                            }
                        }))
                .url().toString();
    }

    public Optional<ObjectInfo> head(String key) {
        try {
            HeadObjectResponse response = client.headObject(request -> request.bucket(config.bucket()).key(key));
            return Optional.of(new ObjectInfo(response.contentLength(), response.contentType(), response.eTag()));
        } catch (NoSuchKeyException missing) {
            return Optional.empty();
        } catch (S3Exception error) {
            if (error.statusCode() == 404) {
                return Optional.empty();
            }
            throw error;
        }
    }

    public void copy(String fromKey, String toKey) {
        client.copyObject(request -> request
                .sourceBucket(config.bucket()).sourceKey(fromKey)
                .destinationBucket(config.bucket()).destinationKey(toKey));
    }

    public void delete(String key) {
        client.deleteObject(request -> request.bucket(config.bucket()).key(key));
    }

    public List<String> listKeys(String prefix) {
        return client.listObjectsV2Paginator(request -> request.bucket(config.bucket()).prefix(prefix))
                .contents().stream().map(object -> object.key()).toList();
    }

    public void writeText(String key, String content) {
        client.putObject(request -> request.bucket(config.bucket()).key(key).contentType("application/json"),
                RequestBody.fromString(content));
    }

    public String readText(String key) {
        return client.getObjectAsBytes(request -> request.bucket(config.bucket()).key(key)).asUtf8String();
    }

    /** Removes everything under a prefix, including anything an attempt uploaded but never reported. */
    public void deletePrefix(String prefix) {
        client.listObjectsV2Paginator(request -> request.bucket(config.bucket()).prefix(prefix))
                .contents()
                .forEach(object -> delete(object.key()));
    }

    @PreDestroy
    void close() {
        client.close();
        browserPresigner.close();
        workerPresigner.close();
    }
}

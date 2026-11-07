package com.motionmirror.support;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.Base64;
import java.util.Map;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.images.builder.Transferable;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/** One shared set of real services per test run, configured from the same files as local development. */
public final class Infrastructure implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    public static final String BROKER_PASSWORD = "test-broker-password";
    public static final String REDIS_PASSWORD = "test-redis-password";
    public static final String S3_ACCESS = "test-access-key";
    public static final String S3_SECRET = "test-secret-key";
    public static final String BUCKET = "motionmirror-test";

    private static final Path REPO = Path.of("../..").toAbsolutePath().normalize();

    public static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer(
            DockerImageName.parse("postgres:18-alpine").asCompatibleSubstituteFor("postgres"));

    public static final GenericContainer<?> RABBIT = new GenericContainer<>("rabbitmq:4.3-management-alpine")
            .withExposedPorts(5672)
            .withCopyToContainer(Transferable.of(read("configs/rabbitmq/rabbitmq.conf")), "/etc/rabbitmq/rabbitmq.conf")
            .withCopyToContainer(Transferable.of(rabbitDefinitions()), "/etc/rabbitmq/definitions.json")
            .waitingFor(Wait.forLogMessage(".*Server startup complete.*", 1).withStartupTimeout(Duration.ofMinutes(2)));

    public static final GenericContainer<?> REDIS = new GenericContainer<>("redis:8-alpine")
            .withExposedPorts(6379)
            .withCopyToContainer(Transferable.of(read("configs/redis/users.template.acl").replace("${MM_REDIS_PASSWORD}", REDIS_PASSWORD)), "/etc/redis/users.acl")
            .withCommand("redis-server", "--aclfile", "/etc/redis/users.acl", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction")
            .waitingFor(Wait.forLogMessage(".*Ready to accept connections.*", 1));

    public static final GenericContainer<?> STORAGE = new GenericContainer<>("chrislusf/seaweedfs:4.52")
            .withExposedPorts(8333)
            .withCopyToContainer(Transferable.of(read("configs/seaweedfs/s3.template.json")
                    .replace("${MM_S3_ACCESS_KEY}", S3_ACCESS).replace("${MM_S3_SECRET_KEY}", S3_SECRET)), "/etc/seaweedfs/s3.json")
            .withCommand("server", "-dir=/data", "-ip.bind=0.0.0.0", "-s3", "-s3.port=8333", "-s3.config=/etc/seaweedfs/s3.json", "-volume.max=4")
            .waitingFor(Wait.forListeningPort().withStartupTimeout(Duration.ofMinutes(2)));

    private static boolean started;

    private static synchronized void startAll() {
        if (started) {
            return;
        }
        POSTGRES.start();
        RABBIT.start();
        REDIS.start();
        STORAGE.start();
        started = true;
    }

    @Override
    public void initialize(ConfigurableApplicationContext context) {
        startAll();
        String storage = storageEndpoint();
        TestPropertyValues.of(Map.ofEntries(
                Map.entry("spring.datasource.url", POSTGRES.getJdbcUrl()),
                Map.entry("spring.datasource.username", POSTGRES.getUsername()),
                Map.entry("spring.datasource.password", POSTGRES.getPassword()),
                Map.entry("spring.rabbitmq.host", RABBIT.getHost()),
                Map.entry("spring.rabbitmq.port", String.valueOf(RABBIT.getMappedPort(5672))),
                Map.entry("spring.rabbitmq.password", BROKER_PASSWORD),
                Map.entry("spring.data.redis.host", REDIS.getHost()),
                Map.entry("spring.data.redis.port", String.valueOf(REDIS.getMappedPort(6379))),
                Map.entry("spring.data.redis.password", REDIS_PASSWORD),
                Map.entry("motionmirror.storage.internal-endpoint", storage),
                Map.entry("motionmirror.storage.public-endpoint", storage),
                Map.entry("motionmirror.storage.worker-endpoint", storage),
                Map.entry("motionmirror.storage.access-key", S3_ACCESS),
                Map.entry("motionmirror.storage.secret-key", S3_SECRET),
                Map.entry("motionmirror.storage.bucket", BUCKET),
                Map.entry("motionmirror.oidc.client-secret", "test-client-secret"))).applyTo(context);
    }

    public static String storageEndpoint() {
        return "http://" + STORAGE.getHost() + ":" + STORAGE.getMappedPort(8333);
    }

    private static String rabbitDefinitions() {
        String template = read("configs/rabbitmq/definitions.template.json");
        for (String name : new String[] {"PROVISION", "PUBLISHER", "CONSUMER_MOCK", "CONSUMER_WAN", "MONITOR"}) {
            template = template.replace("${MM_RABBIT_" + name + "_PASSWORD_HASH}", rabbitHash(BROKER_PASSWORD));
        }
        return template.replace("${MM_RABBIT_CONSUMER_TIMEOUT_MS}", "21600000");
    }

    private static String rabbitHash(String password) {
        try {
            byte[] salt = {1, 2, 3, 4};
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(salt);
            byte[] hash = digest.digest(password.getBytes(StandardCharsets.UTF_8));
            byte[] combined = new byte[salt.length + hash.length];
            System.arraycopy(salt, 0, combined, 0, salt.length);
            System.arraycopy(hash, 0, combined, salt.length, hash.length);
            return Base64.getEncoder().encodeToString(combined);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    public static String read(String relativePath) {
        try {
            return Files.readString(REPO.resolve(relativePath));
        } catch (IOException error) {
            throw new IllegalStateException(error);
        }
    }

    public static Path repoPath(String relativePath) {
        return REPO.resolve(relativePath);
    }
}

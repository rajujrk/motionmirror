package com.motionmirror.assets;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import com.motionmirror.shared.Json;

/**
 * Signs an S3 POST policy with SigV4. Unlike a presigned PUT, the policy makes the storage service enforce
 * the maximum size, the exact key and the content type. The AWS SDK v2 has no helper for this.
 */
public final class PostPolicySigner {

    private static final DateTimeFormatter DATE = DateTimeFormatter.ofPattern("yyyyMMdd").withZone(ZoneOffset.UTC);
    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'").withZone(ZoneOffset.UTC);
    private static final DateTimeFormatter EXPIRY = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC);
    private static final String ALGORITHM = "AWS4-HMAC-SHA256";

    private PostPolicySigner() {}

    public static Map<String, String> sign(String bucket, String key, String contentType, long maxBytes,
            Instant now, Instant expires, String accessKey, String secretKey, String region) {
        String credential = accessKey + "/" + DATE.format(now) + "/" + region + "/s3/aws4_request";
        String stamp = STAMP.format(now);
        Map<String, Object> policy = Map.of(
                "expiration", EXPIRY.format(expires),
                "conditions", List.of(
                        Map.of("bucket", bucket),
                        Map.of("key", key),
                        Map.of("Content-Type", contentType),
                        List.of("content-length-range", 1, maxBytes),
                        Map.of("x-amz-algorithm", ALGORITHM),
                        Map.of("x-amz-credential", credential),
                        Map.of("x-amz-date", stamp)));
        String encoded = Base64.getEncoder().encodeToString(Json.write(policy).getBytes(StandardCharsets.UTF_8));

        Map<String, String> fields = new LinkedHashMap<>();
        fields.put("key", key);
        fields.put("Content-Type", contentType);
        fields.put("x-amz-algorithm", ALGORITHM);
        fields.put("x-amz-credential", credential);
        fields.put("x-amz-date", stamp);
        fields.put("policy", encoded);
        fields.put("x-amz-signature", signature(encoded, now, secretKey, region));
        return fields;
    }

    private static String signature(String stringToSign, Instant now, String secretKey, String region) {
        try {
            byte[] key = hmac(("AWS4" + secretKey).getBytes(StandardCharsets.UTF_8), DATE.format(now));
            key = hmac(key, region);
            key = hmac(key, "s3");
            key = hmac(key, "aws4_request");
            return HexFormat.of().formatHex(hmac(key, stringToSign));
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("cannot sign upload policy", error);
        }
    }

    private static byte[] hmac(byte[] key, String value) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
    }
}

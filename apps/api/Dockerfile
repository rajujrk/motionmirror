# Built from the repository root so contracts and presets are on the classpath.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src/apps/api
COPY apps/api/pom.xml ./
COPY apps/api/.mvn .mvn
COPY apps/api/mvnw ./
RUN ./mvnw -q -B -DskipTests dependency:go-offline
COPY apps/api/src src
COPY packages/contracts /src/packages/contracts
COPY configs/presets /src/configs/presets
RUN ./mvnw -q -B -DskipTests package && java -Djarmode=tools -jar target/api-*.jar extract --destination /app

FROM eclipse-temurin:21-jre-alpine
RUN addgroup -S mm && adduser -S mm -G mm
WORKDIR /app
COPY --from=build /app ./
USER mm
EXPOSE 8081
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=70", "-jar", "/app/api-0.0.1-SNAPSHOT.jar"]

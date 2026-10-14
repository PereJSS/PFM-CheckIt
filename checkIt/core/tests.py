

from rest_framework.test import APITestCase as _APITestCase


class OpenApiDocsTest(_APITestCase):
    """La documentación es pública: la consumen los integradores de PMS."""

    def test_schema_public_and_declares_api_key_scheme(self):
        resp = self.client.get('/api/v1/schema/?format=json')
        self.assertEqual(resp.status_code, 200)
        schemes = resp.json()['components']['securitySchemes']
        self.assertEqual(schemes['ApiKeyAuth']['name'], 'Authorization')
        self.assertIn('ApiKeyAuth', str(resp.json()['paths']['/api/v1/webhooks/pms/']['post']['security']))

    def test_swagger_and_redoc_public(self):
        self.assertEqual(self.client.get('/api/v1/docs/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/redoc/').status_code, 200)

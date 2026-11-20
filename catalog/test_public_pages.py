from django.test import TestCase, override_settings


class PublicReleasePagesTests(TestCase):
    @override_settings(LIBRARY_SUPPORT_EMAIL='help@example.com', LIBRARY_OPERATOR_NAME='<运营者>')
    def test_pages_are_public_and_escape_operator(self):
        for path in ('/privacy/', '/support/', '/terms/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'mailto:help@example.com')
                self.assertContains(response, '&lt;运营者&gt;')
                self.assertNotContains(response, '<运营者>')
                self.assertEqual(self.client.post(path).status_code, 405)

    @override_settings(LIBRARY_SUPPORT_EMAIL='', LIBRARY_OPERATOR_NAME='')
    def test_unconfigured_contact_is_not_invented(self):
        response = self.client.get('/support/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'mailto:')
